"""Monkey 测试 R3 目标 3：线程 / 子进程故障注入（QThread 启动失败、端口占用、
发现文件写失败、服务半途关闭、stop 生命周期矩阵）。

种子：20261023。
角度：control_server 的 run()/stop() 生命周期在故障下必须「降级不挂死」：

  - QThread.start 失败（线程资源耗尽）→ _start_control 降级，不炸 PetWindow 构造；
  - 端口绑定失败 → run() 安全返回，started_ok=False，stop() 无副作用；
  - 发现文件写失败（ENOSPC/EACCES）→ 服务照常提供，stop() 不得死锁（R3-M-02 回归，
    此前 _write_info 抛出后 serve_forever 从未运行，shutdown() 永久阻塞）；
  - 服务半途关闭 → CLI 侧结构化失败（exit 4 + 合法 JSON），不裸 traceback。

oracle：stop() 必须在 5s 内返回；CLI 退出码 ∈ {0,2,3,4} 且 --json 末行合法。
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import cli as cli_mod
from gold_pet import config as config_mod
from gold_pet import control_server as cs

SEED = 20261023
ITERATIONS = 500


def _start_helper(ctl: cs.ControlServer) -> threading.Thread:
    t = threading.Thread(target=ctl.run, daemon=True)
    t.start()
    return t


def _run_with_watchdog(fn, timeout: float = 5.0) -> bool:
    """在线程里跑 fn；返回是否在 timeout 内完成（防挂死测试）。"""
    done = threading.Event()

    def wrapper():
        try:
            fn()
        finally:
            done.set()

    t = threading.Thread(target=wrapper, daemon=True)
    t.start()
    return done.wait(timeout)


def _tmp_info(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cs, "control_info_path", lambda: tmp_path / "control.json")


# ------------------------------------------------------------------ 定向注入
def test_r3_thread_start_failure_degrades(tmp_path, monkeypatch):
    """QThread.start 抛异常（线程资源耗尽）：_start_control 降级，不炸窗口构造路径。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from gold_pet.config import Config
    from gold_pet.price import HistoryStore, PriceService
    from gold_pet.ui import PetWindow

    QApplication.instance() or QApplication([])
    cfg = Config()
    cfg.set("control_enabled", False)
    w = PetWindow(
        cfg,
        PriceService(tmp_path / "l.json"),
        HistoryStore(tmp_path / "h.json"),
        auto_refresh=False,
        notifier_history_path=tmp_path / "a.json",
    )
    try:

        def bad_start(self):
            raise RuntimeError("cannot create thread")

        monkeypatch.setattr(cs.ControlServer, "start", bad_start)
        cfg.set("control_enabled", True)
        w._start_control()  # 不得抛
        assert w.control is None
    finally:
        cfg.set("control_enabled", False)
        w.deleteLater()


def test_r3_thread_bind_failure_safe_return(tmp_path, monkeypatch):
    """端口绑定失败（EADDRINUSE）：run() 安全返回、started_ok=False、stop() 安全。"""
    _tmp_info(monkeypatch, tmp_path)

    def bad_build(*a, **k):
        raise OSError(10048, "address already in use")

    monkeypatch.setattr(cs, "build_server", bad_build)
    ctl = cs.ControlServer(state_reader=lambda: {})
    ctl.run()  # 不得抛
    assert ctl.started_ok is False
    assert ctl._server is None
    assert _run_with_watchdog(ctl.stop, 5.0), "stop() 挂死"


def test_r3_thread_info_write_failure_still_serves_and_stops(tmp_path, monkeypatch):
    """R3-M-02 回归：发现文件写失败后服务仍可用，stop() 5s 内返回（不得死锁）。"""
    _tmp_info(monkeypatch, tmp_path)

    def boom(self):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cs.ControlServer, "_write_info", boom)
    ctl = cs.ControlServer(state_reader=lambda: {"state": "idle"})
    t = _start_helper(ctl)
    deadline = time.perf_counter() + 5.0
    while not ctl.started_ok and time.perf_counter() < deadline:
        time.sleep(0.02)
    assert ctl.started_ok, "服务未启动"
    assert not (tmp_path / "control.json").exists(), "故障下不应写出发现文件"

    # 服务真实可用
    req = urllib.request.Request(
        f"http://127.0.0.1:{ctl.port}/status", headers={"X-GP-Token": ctl.token}
    )
    with urllib.request.urlopen(req, timeout=3) as resp:
        assert resp.status == 200
        assert json.loads(resp.read().decode("utf-8"))["ok"] is True

    assert _run_with_watchdog(ctl.stop, 5.0), "发现文件写失败导致 stop() 死锁"
    t.join(timeout=2)


def test_r3_thread_stop_lifecycle_matrix(tmp_path, monkeypatch):
    """stop 生命周期穷尽矩阵：未启动 / 绑定失败后 / 双 stop / 500 次空 stop 幂等。"""
    _tmp_info(monkeypatch, tmp_path)
    # 1) 未启动直接 stop
    ctl = cs.ControlServer(state_reader=lambda: {})
    assert _run_with_watchdog(ctl.stop, 5.0)

    # 2) 绑定失败后 stop（两次）——用局部 context，别 undo 掉 control_info_path 的隔离
    def bad_build(*a, **k):
        raise OSError(1, "bind failed")

    with monkeypatch.context() as m:
        m.setattr(cs, "build_server", bad_build)
        ctl2 = cs.ControlServer(state_reader=lambda: {})
        ctl2.run()
        assert _run_with_watchdog(ctl2.stop, 5.0)
        assert _run_with_watchdog(ctl2.stop, 5.0)
    # 3) 500 次空 stop：幂等、无异常、快速返回
    started = time.perf_counter()
    for _ in range(ITERATIONS):
        c = cs.ControlServer(state_reader=lambda: {})
        c.stop()
    assert time.perf_counter() - started < 30.0


def test_r3_thread_start_failure_during_window_construction(tmp_path, monkeypatch):
    """构造期（PetWindow.__init__）QThread.start 失败：窗口仍能建出来。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from gold_pet.config import Config
    from gold_pet.price import HistoryStore, PriceService
    from gold_pet.ui import PetWindow

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        cs.ControlServer, "start", lambda self: (_ for _ in ()).throw(RuntimeError("no thread"))
    )
    cfg = Config()  # control_enabled 默认 True → 构造路径直接踩故障
    w = PetWindow(
        cfg,
        PriceService(tmp_path / "l.json"),
        HistoryStore(tmp_path / "h.json"),
        auto_refresh=False,
        notifier_history_path=tmp_path / "a.json",
    )
    try:
        assert w.control is None
        assert w.isWidgetType()
    finally:
        w.deleteLater()


def test_r3_thread_worker_dies_midway_recovers(tmp_path):
    """PriceWorker 线程中途异常死亡：不挂起；下一次 refresh 能重建 worker。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from gold_pet.config import Config
    from gold_pet.price import HistoryStore, PriceService
    from gold_pet.ui import PetWindow

    QApplication.instance() or QApplication([])
    cfg = Config()
    cfg.set("control_enabled", False)
    w = PetWindow(
        cfg,
        PriceService(tmp_path / "l.json"),
        HistoryStore(tmp_path / "h.json"),
        auto_refresh=False,
        notifier_history_path=tmp_path / "a.json",
    )
    try:

        class _DyingService:
            def fetch(self, timeout=8.0):
                raise RuntimeError("worker dies mid-way")

        w.service = _DyingService()
        w.refresh()
        worker = w._worker
        assert worker.wait(5000), "死亡 worker 未及时结束"
        assert not worker.isRunning()
        # 下一次 refresh 不因旧 worker 卡住（isRunning False → 重建）
        w.refresh()
        assert w._worker is not worker
        assert w._worker.wait(5000)
    finally:
        for timer in (w.refresh_timer, w._retry_timer, w._interact_timer):
            timer.stop()
        w.deleteLater()


def test_r3_thread_stop_idempotent_after_run(tmp_path, monkeypatch):
    """真实服务 start→stop×3：端口释放、发现文件清理、无异常。"""
    _tmp_info(monkeypatch, tmp_path)
    ctl = cs.ControlServer(state_reader=lambda: {})
    t = _start_helper(ctl)
    deadline = time.perf_counter() + 5.0
    while not ctl.started_ok and time.perf_counter() < deadline:
        time.sleep(0.02)
    assert ctl.started_ok
    assert (tmp_path / "control.json").exists()
    port = ctl.port
    assert _run_with_watchdog(ctl.stop, 5.0)
    assert not (tmp_path / "control.json").exists()
    assert _run_with_watchdog(ctl.stop, 5.0)
    t.join(timeout=2)
    # 端口已释放：能重新绑定（allow_reuse_address 下 TIME_WAIT 也可复用）
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    finally:
        s.close()


# ------------------------------------------------------------------ CLI 半途关闭
@pytest.fixture()
def cli_paths(tmp_path, monkeypatch):
    mapping = {
        "control_info_path": tmp_path / "control.json",
        "cache_path": tmp_path / "last_price.json",
        "history_path": tmp_path / "history.json",
        "alert_history_path": tmp_path / "alert_history.json",
    }
    for name, path in mapping.items():
        monkeypatch.setattr(cli_mod, name, lambda p=path: p)
    monkeypatch.setattr(config_mod, "settings_path", lambda: tmp_path / "settings.json")
    return mapping


def _write_control_json(path: Path, port: int, token: str) -> None:
    path.write_text(
        json.dumps({"port": port, "token": token, "pid": os.getpid(), "version": "0.8.0"}),
        encoding="utf-8",
    )


def _cli_json(capsys, argv) -> tuple[int, dict]:
    code = cli_mod.main(argv)
    out = capsys.readouterr().out.strip().splitlines()
    data = json.loads(out[-1])
    assert isinstance(data, dict) and isinstance(data.get("ok"), bool)
    return code, data


def test_r3_thread_half_closed_server_cli(cli_paths, capsys):
    """服务被关闭（发现文件仍在、pid 存活）→ CLI exit 4 + 结构化 JSON。"""
    server = cs.build_server(("127.0.0.1", 0), "tok", lambda: {"state": "idle"})
    port = server.server_address[1]
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    server.shutdown()
    server.server_close()  # 端口已关，发现文件残留
    _write_control_json(cli_paths["control_info_path"], port, "tok")

    code, data = _cli_json(capsys, ["status", "--json"])
    assert code == 4
    assert data["ok"] is False
    assert data["error"]["exit_code"] == 4
    assert "控制服务没有响应" in data["error"]["message"]


def test_r3_thread_mid_response_close_cli(cli_paths, capsys):
    """服务端接受连接后在响应中途关闭 → CLI 结构化失败（不裸 traceback）。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen(1)

    def accept_then_truncate():
        try:
            conn, _ = s.accept()
            conn.sendall(b'HTTP/1.0 200 OK\r\nContent-Length: 999\r\n\r\n{"ok":')
            conn.close()
        except OSError:
            pass

    t = threading.Thread(target=accept_then_truncate, daemon=True)
    t.start()
    _write_control_json(cli_paths["control_info_path"], port, "tok")
    try:
        code, data = _cli_json(capsys, ["status", "--json"])
        assert code == 4 and data["ok"] is False
    finally:
        s.close()


def test_r3_thread_401_structured_cli(cli_paths, capsys):
    """服务在线但 token 不匹配（桌宠重启残留）→ exit 4，文案可读。"""
    server = cs.build_server(("127.0.0.1", 0), "real-token", lambda: {"state": "idle"})
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        _write_control_json(cli_paths["control_info_path"], server.server_address[1], "stale-token")
        code, data = _cli_json(capsys, ["pat", "--json"])
        assert code == 4 and data["ok"] is False
        assert "token" in data["error"]["message"].lower()
    finally:
        server.shutdown()
        server.server_close()


def test_r3_thread_garbage_response_cli(cli_paths, capsys):
    """服务返回非 JSON/非对象响应 → exit 4，不炸。"""
    for body in (b"not json at all", b"[1,2,3]", b"null"):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(1)

        def serve(body=body, s=s):
            try:
                conn, _ = s.accept()
                conn.sendall(
                    b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
                conn.close()
            except OSError:
                pass

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        _write_control_json(cli_paths["control_info_path"], port, "tok")
        try:
            code, data = _cli_json(capsys, ["status", "--json"])
            assert code == 4 and data["ok"] is False
        finally:
            s.close()
