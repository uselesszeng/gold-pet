"""Monkey 测试 R2 目标 2：并发面（一轮未覆盖）。

种子：20261012；迭代：≥500（线程内合计 5000+ 操作）。
- HistoryStore.append 10 线程 × 100 次：读-改-写竞态（R2-M-05 修复后
  锁内串行，丢更新必须归零）
- 并发写盘期间的读方：永远看到完整合法 JSON（原子写）
- control_server 8 线程混打不同端点：动作零丢失、请求零失败
- PriceService.save_cache / load_cache 并发交叠：读者不崩、内容恒完整

oracle：无线程异常；文件恒为合法 JSON；计数守恒。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import random
import threading
import time

import httpx
from _monkey_common import assert_valid_json_text

from gold_pet import price as price_mod
from gold_pet.config import Config
from gold_pet.control_server import build_server
from gold_pet.notifier import Alert, Notifier, Quote
from gold_pet.price import HistoryStore, PriceService, Snapshot

SEED = 20261012


def _assert_valid_point_list(series: list) -> None:
    for pt in series:
        assert isinstance(pt, dict) and "t" in pt and "p" in pt


def test_r2_historystore_threaded_append_no_lost_updates(tmp_path):
    """10 线程 × 40 次 append（注入时钟拉开 61s 间隔）：锁内串行，零丢更新。"""
    hs = HistoryStore(tmp_path / "history.json", max_points=1440)
    base = time.time()
    clock = {"t": base}
    clock_lock = threading.Lock()

    def fake_time():
        with clock_lock:
            clock["t"] += 61.0
            return clock["t"]

    orig_time = price_mod.time.time
    price_mod.time.time = fake_time
    errors: list[str] = []
    try:
        def worker(tid: int) -> None:
            try:
                for _ in range(40):
                    hs.append(f"m{tid}", "cny_g", 700.0 + tid)
            except Exception as exc:  # noqa: BLE001 - 收集后统一断言
                errors.append(f"t{tid}: {type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        price_mod.time.time = orig_time

    assert not errors, errors[:5]
    data = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    # R2-M-05 修复前：读-改-写竞态导致 10 个键只剩 1~3 个
    assert len(data) == 10, f"丢更新：只剩 {len(data)} 个序列"
    for series in data.values():
        assert len(series) == 40
        _assert_valid_point_list(series)


def test_r2_historystore_reader_never_sees_torn_file(tmp_path):
    """并发 append 期间读方循环 json.loads：永远完整合法（原子写，R2-M-05）。"""
    hs = HistoryStore(tmp_path / "history.json")
    hs.append("gold", "cny_g", 700.0)
    stop = threading.Event()
    read_errors: list[str] = []
    reads = {"n": 0}

    def reader() -> None:
        while not stop.is_set():
            try:
                text = (tmp_path / "history.json").read_text(encoding="utf-8")
                json.loads(text)
                reads["n"] += 1
            except ValueError as exc:
                read_errors.append(str(exc)[:100])
            except OSError:
                pass  # Windows 换文件瞬间的共享冲突属文件系统域

    rt = threading.Thread(target=reader, daemon=True)
    rt.start()

    def writer(tid: int) -> None:
        for i in range(25):
            hs.append(f"k{tid}", "usd_oz", 500.0 + i * 0.1)

    try:
        threads = [threading.Thread(target=writer, args=(t,)) for t in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        stop.set()
        rt.join(timeout=5)

    assert not read_errors, f"读者看到撕裂/坏文件 {len(read_errors)} 次: {read_errors[:3]}"
    assert reads["n"] > 0



def test_r2_price_cache_concurrent_write_read(tmp_path):
    """4 写 4 读并发 250 轮：load_cache 恒不炸、内容恒完整（要么旧要么新快照）。"""
    svc = PriceService(tmp_path / "last.json")

    def snap(price: float) -> Snapshot:
        q = Quote(code="gds_AUTD", name="x", price=price, prev_close=price - 1, high=price, low=price - 2)
        return Snapshot(quotes={"gold|cny_g": q}, fx=7.1, updated_at=time.time(), ok=True)

    errors: list[str] = []

    def writer(tid: int) -> None:
        try:
            for i in range(250):
                svc.save_cache(snap(700.0 + tid * 1000 + i))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"w{tid}: {type(exc).__name__}: {exc}")

    def reader(tid: int) -> None:
        try:
            for _ in range(250):
                s = svc.load_cache()
                if s is not None:
                    assert isinstance(s.fx, float)
                    for q in s.quotes.values():
                        assert q.price == q.price  # 非 NaN
        except Exception as exc:  # noqa: BLE001
            errors.append(f"r{tid}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
    threads += [threading.Thread(target=reader, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:5]
    final = svc.load_cache()
    assert final is not None and final.ok


def test_r2_notifier_record_file_consistent_under_threads(tmp_path):
    """多线程 record（单写者语义未加锁）：文件永远完整合法、条数永不越界；
    全部 join 后磁盘与内存一致（最后一次 _save 必然包含全部 extend）。"""
    n = Notifier(Config({}), history_path=tmp_path / "ah.json", max_history=200)
    errors: list[str] = []

    def worker(tid: int) -> None:
        try:
            for i in range(100):
                n.record([Alert(kind="pct", side="up", title="t", body=f"b{tid}-{i}",
                                at=1700000000.0 + tid * 100 + i)])
                try:
                    text = (tmp_path / "ah.json").read_text(encoding="utf-8")
                except OSError:
                    continue  # Windows replace 瞬间 delete-pending，属文件系统域
                assert_valid_json_text(text)
                on_disk = json.loads(text)
                assert len(on_disk) <= 200
        except Exception as exc:  # noqa: BLE001
            errors.append(f"t{tid}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:5]
    on_disk = json.loads((tmp_path / "ah.json").read_text(encoding="utf-8"))
    assert len(on_disk) <= 200
    assert on_disk == [a.to_dict() for a in n._history]


TOKEN = "monkey2-conc"


def _state_reader() -> dict:
    return {"app": "gold-pet", "version": "0.8.0", "state": "idle", "fatty_count": 0}


def test_r2_control_server_threaded_endpoints():
    """8 线程混打 /say /move /play /status × 80 轮：零失败、动作计数守恒、全程 ≤2s。"""
    actions: list[dict] = []
    server = build_server(("127.0.0.1", 0), TOKEN, _state_reader)
    server.on_action = actions.append
    th = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    rnd = random.Random(SEED + 1)
    fails: list[str] = []

    def hit(tid: int) -> None:
        with httpx.Client(base_url=base, headers={"X-GP-Token": TOKEN}, timeout=2.0) as s:
            try:
                for _ in range(80):
                    r = s.post("/say", json={"text": f"t{tid} " + "x" * rnd.randint(1, 50)})
                    assert r.status_code == 200
                    r = s.post("/move", json={"dx": rnd.randint(-100, 100), "dy": rnd.randint(-100, 100)})
                    assert r.status_code == 200
                    r = s.post("/play", json={"state": "idle"})
                    assert r.status_code == 200
                    t0 = time.perf_counter()
                    r = s.get("/status")
                    assert r.status_code == 200
                    assert time.perf_counter() - t0 < 2.0
            except Exception as exc:  # noqa: BLE001
                fails.append(f"t{tid}: {type(exc).__name__}: {exc}")

    try:
        threads = [threading.Thread(target=hit, args=(t,)) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        server.shutdown()
        server.server_close()

    assert not fails, fails[:5]
    assert len(actions) == 8 * 80 * 3, f"动作丢失: {len(actions)} != {8 * 80 * 3}"
    for a in actions:
        json.dumps(a, ensure_ascii=False)  # 动作恒可序列化
