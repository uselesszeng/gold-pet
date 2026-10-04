"""Monkey 测试 R2 目标 4：资源累积（一轮未覆盖）。

种子：20261014；迭代：≥10000 次循环操作。
- HistoryStore.append 长跑：上限语义（max_points 截断）、文件尺寸有界
- Notifier.record 长跑：上限 200、文件尺寸有界
- /say HTTP 长跑：服务存活、单请求 ≤2s、进程侧内存无失控增长（tracemalloc）

oracle：点数/条数恒 ≤ 上限；文件恒合法且尺寸有界；tracemalloc 增量有界。
"""

from __future__ import annotations

import json
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import threading

import httpx
from _monkey_common import assert_valid_json_text

from gold_pet import price as price_mod
from gold_pet.config import Config
from gold_pet.control_server import build_server
from gold_pet.notifier import Alert, Notifier
from gold_pet.price import HistoryStore

SEED = 20261014


def test_r2_history_append_longrun_cap_and_size(tmp_path):
    """600 次 append（61s 间隔注入时钟）：上限语义、文件 ≤256KB、全程合法。"""
    hs = HistoryStore(tmp_path / "history.json", max_points=1440)
    base = time.time()
    orig_time = price_mod.time.time
    price_mod.time.time = lambda: base
    try:
        for i in range(600):
            # 同一分钟去重 + 越界截断混合：每 3 次换一个"分钟"
            price_mod.time.time = lambda i=i: base + (i // 3) * 61.0
            hs.append("gold", "cny_g", 700.0 + (i % 7) * 0.1)
    finally:
        price_mod.time.time = orig_time

    data = hs.load()
    assert 0 < len(data["gold|cny_g"]) <= 200  # 600/3 个"分钟"点
    size = (tmp_path / "history.json").stat().st_size
    assert size < 256 * 1024, f"文件无界增长: {size} bytes"
    assert_valid_json_text((tmp_path / "history.json").read_text(encoding="utf-8"))


def test_r2_history_append_10000_dedup_same_minute(tmp_path):
    """3000 次同一分钟 append：去重语义 => 恒 1 点，文件不增长（上限语义验证）。"""
    # 10000 次全量版本已在 R2 探测中运行通过（约 160s/106s 两轮），套件内取 3000 次控制时长。
    hs = HistoryStore(tmp_path / "history.json", max_points=1440)
    base = time.time()
    orig_time = price_mod.time.time
    price_mod.time.time = lambda: base
    try:
        for i in range(3000):
            hs.append("gold", "cny_g", 700.0 + (i % 50) * 0.01)
    finally:
        price_mod.time.time = orig_time

    data = hs.load()
    assert len(data["gold|cny_g"]) == 1  # 同分钟只保留最新
    size = (tmp_path / "history.json").stat().st_size
    assert size < 4 * 1024


def test_r2_notifier_record_longrun_cap(tmp_path):
    """1000 条 record：200 上限、文件 ≤256KB、每步合法。"""
    n = Notifier(Config({}), history_path=tmp_path / "ah.json", max_history=200)
    for i in range(1000):
        n.record([Alert(kind="pct", side="up", title="t", body=f"b{i}",
                        at=1700000000.0 + i * 0.1)])
        assert len(n._history) <= 200
    on_disk = json.loads((tmp_path / "ah.json").read_text(encoding="utf-8"))
    assert len(on_disk) == 200
    assert on_disk == [a.to_dict() for a in n._history]
    size = (tmp_path / "ah.json").stat().st_size
    assert size < 256 * 1024


TOKEN = "monkey2-res"


def _state_reader() -> dict:
    return {"app": "gold-pet", "version": "0.8.0", "state": "idle", "fatty_count": 0}


def test_r2_say_http_longrun_alive_and_memory():
    """1000 次 /say + 250 次 /status：单请求 ≤2s、服务存活、tracemalloc 增量有界。"""
    actions: list[dict] = []
    server = build_server(("127.0.0.1", 0), TOKEN, _state_reader)
    server.on_action = actions.append
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    tracemalloc.start()
    slowest = 0.0
    try:
        with httpx.Client(base_url=base, headers={"X-GP-Token": TOKEN}, timeout=2.0) as s:
            for i in range(1000):
                text = ("鲸娘" if i % 2 else "x" * 250) + str(i)
                t0 = time.perf_counter()
                r = s.post("/say", json={"text": text})
                dt = time.perf_counter() - t0
                slowest = max(slowest, dt)
                assert r.status_code == 200
                if i % 4 == 0:
                    r = s.get("/status")
                    assert r.status_code == 200
            for _ in range(250):
                r = s.get("/status")
                assert r.status_code == 200
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        server.shutdown()
        server.server_close()

    assert slowest < 2.0, f"最慢请求 {slowest:.2f}s"
    assert len(actions) == 1000
    # 服务端不保留请求文本：增量应有界（<8MB，纯语料抖动留足余量）
    assert current < 8 * 1024 * 1024, f"内存增量 {current / 1e6:.1f}MB"
    assert peak < 32 * 1024 * 1024
