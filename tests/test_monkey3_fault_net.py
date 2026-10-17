"""Monkey 测试 R3 目标 2：网络故障注入（超时 / 5xx / 断连 / 慢响应 / 脏上游数值）。

种子：20261022。
角度：本机真实 HTTP 服务（127.0.0.1 随机端口）扮演新浪行情源，按脚本返回
超时前慢响应、HTTP 500、直接断连、半截报文、GBK 乱码、非有限数值字段、
超大包、302 重定向；PriceService.fetch 与 UI PriceWorker 退避链必须结构化降级。

oracle：
  - fetch() 永不抛异常，返回 Snapshot；失败时 error 非空且 ok=False；
  - 失败不覆盖既有合法缓存（缓存文件 byte 级不变）；
  - 写盘后 last_price.json 必须是严格 JSON（NaN/Infinity 字面量被拒）；
  - 退避链 15→30→60→120→240→300→300…（cap=300），成功后 _fail_streak 归零；
  - 单请求 ≤3s。
"""

from __future__ import annotations

import random
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from _monkey_common import assert_valid_json_text

from gold_pet import price as price_mod
from gold_pet.config import Config
from gold_pet.price import PriceService, Snapshot

SEED = 20261022
ITERATIONS = 500

_GOLD = "897.15,0,896.03,897.25,902.00,889.24,15:29:54,900.37,897.00,45270,2,7,2026-09-29,黄金延期"
_FX = "16:37:00,6.7038,6.7042,6.7118,61,6.7094,6.7094,6.7033,6.7042,x,-0.11,0,0,y,0,0,,2026-09-29"


def _payload(gold: str = _GOLD, fx: str = _FX) -> str:
    return f'var hq_str_gds_AUTD="{gold}";\nvar hq_str_fx_susdcny="{fx}";\n'


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):  # noqa: N802
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=gbk") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/ok":
            self._send(200, _payload().encode("gbk"))
        elif path == "/partial":
            raw = _payload().encode("gbk")
            self._send(200, raw[: len(raw) // 2])
        elif path == "/nofinite":
            bad = "1e999,0,896.03,897.25,902.00,889.24,15:29:54,900.37,897.00,45270,2,7,2026-09-29,黄金延期"
            self._send(200, _payload(gold=bad).encode("gbk"))
        elif path == "/nanmix":
            bad = "nan,0,896.03,897.25,902.00,889.24,15:29:54,inf,897.00,45270,2,7,2026-09-29,黄金延期"
            self._send(200, _payload(gold=bad).encode("gbk"))
        elif path == "/err500":
            self._send(500, b"upstream broken")
        elif path == "/garbage":
            self._send(200, b"\xff\xfe\x00\x81 not gbk at all")
        elif path == "/empty":
            self._send(200, b"")
        elif path == "/slow":
            time.sleep(0.8)
            self._send(200, _payload().encode("gbk"))
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/reset":
            self.connection.close()
        elif path == "/megabody":
            self._send(200, b"A" * (1024 * 1024))
        else:
            self._send(404, b"nope")


@pytest.fixture()
def sina(tmp_path, monkeypatch):
    """起本机行情服务并把 price.SINA_URL 指过去；返回 (base_url, cache_path)。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(price_mod, "SINA_URL", base + "/ok")
    yield base, tmp_path / "last_price.json"
    server.shutdown()
    server.server_close()


def _point_at(monkeypatch, sina, path: str) -> None:
    base, _ = sina
    monkeypatch.setattr(price_mod, "SINA_URL", base + path)


# ------------------------------------------------------------------ 定向矩阵
@pytest.mark.parametrize(
    "path",
    [
        "/ok",
        "/partial",
        "/nofinite",
        "/nanmix",
        "/err500",
        "/garbage",
        "/empty",
        "/megabody",
        "/redirect",
        "/reset",
    ],
)
def test_r3_net_fetch_matrix(sina, monkeypatch, path):
    """10 种上游行为：fetch 永不抛、结构合法、写盘严格 JSON、失败不覆盖好缓存。"""
    _point_at(monkeypatch, sina, path)
    _, cache = sina
    svc = PriceService(cache)

    # 先写一份合法缓存，失败响应不得覆盖它
    good_svc = PriceService(cache)
    _point_at(monkeypatch, sina, "/ok")
    good = good_svc.fetch(timeout=2)
    assert good.ok, good.error
    before = cache.read_text(encoding="utf-8")

    _point_at(monkeypatch, sina, path)
    started = time.perf_counter()
    snap = svc.fetch(timeout=0.4 if path == "/slow" else 2.0)
    elapsed = time.perf_counter() - started

    assert isinstance(snap, Snapshot)
    assert elapsed <= 3.0, f"{path} 超过 3s"
    if path == "/ok" or path == "/redirect":
        assert snap.ok and snap.quotes
    elif path == "/partial":
        # 半截报文：能解析出多少就用多少（产品语义是降级而非失败），
        # 但结构必须合法、缓存必须严格 JSON
        assert isinstance(snap.ok, bool)
        for q in snap.quotes.values():
            assert q.price == q.price
    elif path == "/nofinite" or path == "/nanmix":
        # R3-M-01：非有限字段按坏值剔除；不得进入报价，也不得把非法字面量写盘
        for q in snap.quotes.values():
            assert q.price == q.price and abs(q.price) != float("inf")
        if snap.ok:
            assert cache.read_text(encoding="utf-8") != before  # 成功才允许更新
    else:
        assert not snap.ok, f"{path} 应失败"
        assert snap.error, f"{path} 失败必须带 error"
        assert cache.read_text(encoding="utf-8") == before, f"{path} 覆盖了合法缓存"
    if cache.exists():
        assert_valid_json_text(cache.read_text(encoding="utf-8"))


def test_r3_net_slow_timeout_bounded(sina, monkeypatch):
    """/slow 0.8s + timeout=0.2：按超时失败，不挂起（≤2s）。"""
    _point_at(monkeypatch, sina, "/slow")
    _, cache = sina
    svc = PriceService(cache)
    started = time.perf_counter()
    snap = svc.fetch(timeout=0.2)
    elapsed = time.perf_counter() - started
    assert elapsed <= 2.0
    assert not snap.ok and snap.error
    assert not cache.exists()


def test_r3_net_nonfinite_upstream_keeps_cache_strict(sina, monkeypatch):
    """R3-M-01 回归：上游 1e999/nan/inf 不得落盘 Infinity/NaN 字面量。"""
    _point_at(monkeypatch, sina, "/nofinite")
    _, cache = sina
    svc = PriceService(cache)
    snap = svc.fetch(timeout=2)
    assert not snap.ok, "非有限字段应被视为无有效行情"
    if cache.exists():
        assert_valid_json_text(cache.read_text(encoding="utf-8"))
        assert "Infinity" not in cache.read_text(encoding="utf-8")

    _point_at(monkeypatch, sina, "/nanmix")
    snap2 = svc.fetch(timeout=2)
    assert not snap2.ok
    text = cache.read_text(encoding="utf-8") if cache.exists() else "{}"
    assert_valid_json_text(text)


def test_r3_net_error_strings_are_structured(sina, monkeypatch):
    """/err500 与 /reset 的 error 必须是可展示字符串（不含裸 traceback）。"""
    _, cache = sina
    svc = PriceService(cache)
    for path in ("/err500", "/reset"):
        _point_at(monkeypatch, sina, path)
        snap = svc.fetch(timeout=1)
        assert not snap.ok
        assert isinstance(snap.error, str) and snap.error
        assert "Traceback" not in snap.error


# ------------------------------------------------------------------ PriceWorker 退避链
def _window(tmp_path, interval: int = 15):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from gold_pet.price import HistoryStore
    from gold_pet.ui import PetWindow

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    cfg = Config()
    cfg.set("control_enabled", False)  # 测试不写真实 %APPDATA%，不遗留 QThread
    cfg.set("interval_sec", interval)
    w = PetWindow(
        cfg,
        PriceService(tmp_path / "last.json"),
        HistoryStore(tmp_path / "history.json"),
        auto_refresh=False,
        notifier_history_path=tmp_path / "alert_history.json",
    )
    return app, w


def test_r3_net_worker_backoff_chain(tmp_path):
    """失败退避链：15→30→60→120→240→300→300…，成功后归零并恢复常规间隔。"""
    _, w = _window(tmp_path, interval=15)
    expected = [15, 30, 60, 120, 240, 300, 300, 300, 300, 300]
    try:
        for want in expected:
            w._on_snapshot(Snapshot(ok=False, error="boom"))
            assert w._retry_timer.isActive()
            assert w._retry_timer.interval() == want * 1000, (want, w._retry_timer.interval())
        # 成功一次：退避归零，恢复常规刷新间隔（生产路径中 refresh() 会先停重试定时器）
        from gold_pet.price import Quote

        good = Snapshot(
            quotes={
                "gold|cny_g": Quote(
                    code="x",
                    name="黄金",
                    price=900.0,
                    prev_close=899.0,
                    high=901.0,
                    low=898.0,
                    unit="元/克",
                )
            },
            fx=6.7,
            ok=True,
            error="",
        )
        w._retry_timer.stop()  # 模拟 refresh() 入口行为，再走成功回调
        w._on_snapshot(good)
        assert w._fail_streak == 0
        assert not w._retry_timer.isActive()
        assert w.refresh_timer.isActive() and w.refresh_timer.interval() == 15 * 1000
        # 再失败从第一档重新开始
        w._on_snapshot(Snapshot(ok=False, error="boom2"))
        assert w._retry_timer.interval() == 15 * 1000
    finally:
        w._retry_timer.stop()
        w.deleteLater()


@pytest.mark.parametrize("interval,expected_first", [(30, 30), (60, 60), (300, 300), (900, 300)])
def test_r3_net_worker_backoff_cap(tmp_path, interval, expected_first):
    """cap=300s：interval 30/60/300/900 的首档与上限都不得越界（≥15s）。"""
    _, w = _window(tmp_path, interval=interval)
    try:
        w._on_snapshot(Snapshot(ok=False, error="boom"))
        assert w._retry_timer.interval() == expected_first * 1000
        for _ in range(12):
            w._on_snapshot(Snapshot(ok=False, error="boom"))
        assert w._retry_timer.interval() == 300 * 1000
    finally:
        w._retry_timer.stop()
        w.deleteLater()


def test_r3_net_worker_thread_end_to_end(tmp_path, sina, monkeypatch):
    """PriceWorker 真实线程跑本机行情源：snapshot_ready 必须携带 Snapshot。"""
    _, cache = sina
    _point_at(monkeypatch, sina, "/ok")
    _, w = _window(tmp_path)
    from PySide6.QtCore import Qt

    from gold_pet.ui import PriceWorker

    got: list = []
    worker = PriceWorker(PriceService(cache))
    # 直连：测试不跑事件循环，跨线程队列投递不会被执行
    worker.snapshot_ready.connect(got.append, Qt.ConnectionType.DirectConnection)
    try:
        worker.start()
        assert worker.wait(8000), "worker 超时未结束"
        assert len(got) == 1 and isinstance(got[0], Snapshot)
        assert got[0].ok
    finally:
        worker.deleteLater()
        w.deleteLater()


# ------------------------------------------------------------------ 随机故障混合
def test_r3_net_random_fault_mix(sina, monkeypatch):
    """500 次随机上游行为/超时组合：无异常逃逸、返回值结构合法、≤3s。"""
    rnd = random.Random(SEED)
    base, cache = sina
    paths = [
        "/ok",
        "/partial",
        "/nofinite",
        "/nanmix",
        "/err500",
        "/garbage",
        "/empty",
        "/redirect",
        "/megabody",
        "/404",
        "/ok",
        "/ok",
    ]
    svc = PriceService(cache)
    for i in range(ITERATIONS):
        path = rnd.choice(paths)
        timeout = rnd.choice([0.05, 0.2, 1.0, 2.0])
        monkeypatch.setattr(price_mod, "SINA_URL", base + path)
        started = time.perf_counter()
        try:
            snap = svc.fetch(timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"迭代 {i} {path} fetch 抛 {type(exc).__name__}: {exc}")
        assert time.perf_counter() - started <= 3.0, f"迭代 {i} 超时"
        assert isinstance(snap, Snapshot)
        assert isinstance(snap.ok, bool)
        if snap.ok:
            for q in snap.quotes.values():
                assert q.price == q.price  # 非 NaN
        if cache.exists():
            assert_valid_json_text(cache.read_text(encoding="utf-8"))


def test_r3_net_socket_reset_variants(sina, monkeypatch, tmp_path):
    """极端断连变体：向已关闭连接发请求 / 服务半途关闭 socket。"""
    _, cache = sina
    svc = PriceService(cache)
    # 未监听端口（连接被拒）
    monkeypatch.setattr(price_mod, "SINA_URL", "http://127.0.0.1:1/none")
    snap = svc.fetch(timeout=1)
    assert not snap.ok and snap.error
    # 打开后立刻关闭的裸 socket 端口
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen(1)

    def accept_and_die():
        try:
            conn, _ = s.accept()
            conn.close()
        except OSError:
            pass

    t = threading.Thread(target=accept_and_die, daemon=True)
    t.start()
    monkeypatch.setattr(price_mod, "SINA_URL", f"http://127.0.0.1:{port}/x")
    snap2 = svc.fetch(timeout=1)
    assert not snap2.ok and snap2.error
    s.close()
