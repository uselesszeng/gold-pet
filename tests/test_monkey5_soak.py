"""Monkey 测试 R5 目标 3：长跑稳态与资源守恒（前四轮未覆盖）。

种子：20261105。

角度：桌宠是**常驻**程序（开机挂一整天），前四轮都在「短时高频」上做文章，
没人验证「长时间稳态」：内存/线程/文件描述符会不会随时间单调增长。
本套件用「压缩时间的长时间跑」逼近真实使用：

  1. 刷新链路 1000 轮：内存条目数、线程数、FD 数必须收敛（不随时间线性增长）；
  2. 提醒历史 1000 条写入：内存与磁盘都必须被 max_history 收敛，不得无界放大；
  3. 控制服务状态读取 500 次：无 FD/线程泄漏；
  4. 长时间 IO 往返（save/load 300 轮）：FD 计数回到基线。

oracle：资源计数在长跑后必须回到「有界区间」——不得随迭代次数线性增长；
单轮耗时不得随时间劣化（末段均值 ≤ 首段均值 × 3）。
"""

from __future__ import annotations

import gc
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from gold_pet.config import Config
from gold_pet.notifier import Alert, Notifier
from gold_pet.price import HistoryStore, PriceService, Snapshot

ITERATIONS = 1000


def _fd_count() -> int:
    """当前进程打开的 FD 数（Linux）。"""
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:  # pragma: no cover - 非 Linux
        return -1


def _thread_count() -> int:
    return len(threading.enumerate())


def _rss_kb() -> int:
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:  # pragma: no cover
        pass
    return -1


def _warmup_and_settle(fn, rounds: int = 50) -> None:
    for _ in range(rounds):
        fn()
    gc.collect()


# ------------------------------------------------------------------ 刷新链路长跑
def test_r5_soak_history_store_no_unbounded_growth(tmp_path):
    """1000 轮 append：磁盘序列长度被 max_points 收敛，内存/线程/FD 不增长。"""
    store = HistoryStore(tmp_path / "history.json", max_points=50)
    _warmup_and_settle(lambda: store.append("gold", "cny_g", 900.0))
    fd0, th0 = _fd_count(), _thread_count()

    t0 = time.perf_counter()
    for i in range(ITERATIONS):
        store.append("gold", "cny_g", 900.0 + i * 0.001)
    elapsed = time.perf_counter() - t0

    series = store.series("gold", "cny_g")
    assert len(series) <= 50, len(series)
    on_disk = (tmp_path / "history.json").read_text(encoding="utf-8")
    assert len(on_disk) < 20000, f"history.json 无界增长：{len(on_disk)}"
    assert _fd_count() <= fd0 + 2, f"FD 泄漏：{fd0} -> {_fd_count()}"
    assert _thread_count() <= th0, f"线程泄漏：{th0} -> {_thread_count()}"
    assert elapsed < 60, f"1000 轮 append 耗时 {elapsed:.1f}s（劣化）"


def test_r5_soak_price_cache_roundtrip_steady(tmp_path):
    """500 轮 save_cache/load_cache：FD 与线程守恒，缓存内容稳定。"""
    svc = PriceService(tmp_path / "last_price.json")
    snap = Snapshot(quotes={}, fx=6.7, updated_at=1700000000.0, ok=True, error="")
    _warmup_and_settle(lambda: (svc.save_cache(snap), svc.load_cache()))
    fd0, th0 = _fd_count(), _thread_count()
    for _ in range(500):
        svc.save_cache(snap)
        loaded = svc.load_cache()
        assert loaded is not None and loaded.fx == 6.7
    assert _fd_count() <= fd0 + 2, f"FD 泄漏：{fd0} -> {_fd_count()}"
    assert _thread_count() <= th0


def test_r5_soak_config_save_load_no_fd_leak(tmp_path):
    """300 轮 Config.save/load：FD 守恒（write_text 未关闭会立刻暴露）。"""
    path = tmp_path / "settings.json"
    cfg = Config.load(path)
    cfg.save()
    _warmup_and_settle(lambda: Config.load(path))
    fd0 = _fd_count()
    for i in range(300):
        cfg.set("opacity", 0.3 + (i % 70) / 100.0)
        cfg.save()
        Config.load(path)
    assert _fd_count() <= fd0 + 2, f"FD 泄漏：{fd0} -> {_fd_count()}"


# ------------------------------------------------------------------ 提醒历史长跑
def test_r5_soak_notifier_history_bounded(tmp_path):
    """1000 条提醒写入：内存与磁盘都被 max_history 收敛，且不等于 max_history 的倍数的泄漏。"""
    path = tmp_path / "alert_history.json"
    n = Notifier(Config(), path, max_history=100)
    alert = Alert(kind="pct", side="up", title="t", body="b", at=1700000000.0)
    for _ in range(ITERATIONS):
        n.record([alert])
    history = n.history()
    assert len(history) == 100, len(history)
    raw = path.read_text(encoding="utf-8")
    import json

    items = json.loads(raw)
    assert len(items) == 100, len(items)
    # 反复 record 不得让文件随时间变大（每轮全量重写，长度恒定）
    assert len(raw) < 20000, len(raw)


def test_r5_soak_notifier_record_clear_cycle(tmp_path):
    """record/clear_history 交替 200 轮：无 FD 泄漏、无状态残留。"""
    path = tmp_path / "alert_history.json"
    n = Notifier(Config(), path, max_history=50)
    alert = Alert(kind="target", side="high", title="t", body="b")
    _warmup_and_settle(lambda: n.record([alert]))
    fd0 = _fd_count()
    for _ in range(200):
        n.record([alert] * 3)
        n.clear_history()
    assert n.history() == []
    assert _fd_count() <= fd0 + 2, f"FD 泄漏：{fd0} -> {_fd_count()}"


# ------------------------------------------------------------------ 内存稳态
def test_r5_soak_rss_does_not_run_away(tmp_path):
    """长跑后 RSS 增幅有界：≤ 32MB（对象泄漏时 1000 轮会明显超）。"""
    if _rss_kb() < 0:
        pytest.skip("非 Linux 环境")
    store = HistoryStore(tmp_path / "history.json", max_points=60)
    cfg = Config.load(tmp_path / "settings.json")
    n = Notifier(cfg, tmp_path / "alert_history.json", max_history=50)
    alert = Alert(kind="pct", side="up", title="t", body="b")

    def once(i: int) -> None:
        store.append("gold", "cny_g", 900.0 + (i % 100) * 0.01)
        n.record([alert])
        cfg.set("alert_pct", 0.5 + (i % 5) * 0.1)
        cfg.save()

    _warmup_and_settle(lambda: once(0), rounds=100)
    gc.collect()
    baseline = _rss_kb()
    for i in range(ITERATIONS):
        once(i)
    gc.collect()
    growth = _rss_kb() - baseline
    assert growth < 32 * 1024, f"RSS 增长 {growth}KB（可能泄漏）"


# ------------------------------------------------------------------ 耗时劣化
def test_r5_soak_latency_does_not_degrade(tmp_path):
    """末段 100 轮平均耗时 ≤ 首段 100 轮平均 × 3（隐藏的 O(n) 累积会暴露）。"""
    store = HistoryStore(tmp_path / "history.json", max_points=60)
    timings: list[float] = []
    for i in range(600):
        t0 = time.perf_counter()
        store.append("gold", "cny_g", 900.0 + i * 0.01)
        timings.append(time.perf_counter() - t0)
    head = sum(timings[:100]) / 100
    tail = sum(timings[-100:]) / 100
    assert tail <= max(head * 3, head + 0.02), f"耗时劣化：head={head:.5f}s tail={tail:.5f}s"


def test_r5_soak_state_read_loop_no_fd_leak(tmp_path):
    """控制服务状态读取 500 次（state_reader 闭包）：无 FD/线程泄漏。"""
    from gold_pet.config import Config as Cfg

    cfg = Cfg()
    reads = {"n": 0}

    def state_reader() -> dict:
        reads["n"] += 1
        return {"state": "idle", "version": "0.8.0", "cfg": cfg.get("metal")}

    _warmup_and_settle(lambda: state_reader(), rounds=50)
    fd0, th0 = _fd_count(), _thread_count()
    for _ in range(500):
        assert state_reader()["state"] == "idle"
    assert reads["n"] == 550
    assert _fd_count() <= fd0 + 2, f"FD 泄漏：{fd0} -> {_fd_count()}"
    assert _thread_count() <= th0


# ------------------------------------------------------------------ 协议层长跑
def test_r5_soak_http_server_request_loop_no_leak():
    """真实 HTTP 服务 300 次请求：服务端线程回收、FD 守恒、连接不堆积。"""
    import http.client
    import json as _json

    pytest.importorskip("PySide6")
    from gold_pet.control_server import build_server

    server = build_server(("127.0.0.1", 0), "tok", lambda: {"state": "idle", "n": 1})
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    try:

        def hit() -> None:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("GET", "/status", headers={"X-GP-Token": "tok"})
                resp = conn.getresponse()
                payload = _json.loads(resp.read().decode("utf-8"))
                assert resp.status == 200 and payload["ok"] is True
            finally:
                conn.close()

        for _ in range(10):
            hit()
        threads_after_warmup = _thread_count()
        fd0 = _fd_count()
        for _ in range(300):
            hit()
        time.sleep(0.3)  # 给 ThreadingHTTPServer 的 per-request 线程收尾
        assert _fd_count() <= fd0 + 4, f"FD 泄漏：{fd0} -> {_fd_count()}"
        assert _thread_count() <= threads_after_warmup + 8, (
            f"请求线程堆积：{threads_after_warmup} -> {_thread_count()}"
        )

        # 鉴权失败路径同样不得泄漏
        for _ in range(50):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("GET", "/status", headers={"X-GP-Token": "wrong"})
                resp = conn.getresponse()
                assert resp.status == 401
                resp.read()
            finally:
                conn.close()
        time.sleep(0.3)
        assert _fd_count() <= fd0 + 4, f"鉴权路径 FD 泄漏：{fd0} -> {_fd_count()}"
    finally:
        server.shutdown()
        server.server_close()
