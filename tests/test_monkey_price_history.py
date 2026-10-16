"""Monkey 测试目标 2：price.py HistoryStore.load / append 脏数据轰炸。

种子：20261003；迭代：load 500 次 + append 500 次。
语料：随机 JSON 结构（深层嵌套/混合类型/10 万点超长序列/怪键名/重复键/
NaN-Infinity 字面量/非法 UTF-8/截断 JSON），80% 非法 / 20% 合法边界。

oracle：不抛异常、返回结构合法（dict[str, list[dict]]，点内 t/p 均为有限 float），
每次写盘后状态文件必须是合法 JSON 文本（不得含 NaN/Infinity 非法字面量），
单次调用 ≤2s。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)  # 项目根入 sys.path（同 test_main.py 惯例）

import json
import random
import time

import pytest
from _monkey_common import (
    assert_valid_json_text,
    deep_nested_json,
    dirty_value,
    json_with_nan,
    pick_number,
    truncated_json,
    weird_bytes,
)

from gold_pet.price import HistoryStore

SEED = 20261003
ITERATIONS = 500
CALL_BUDGET_S = 2.0


@pytest.fixture()
def hist_path(tmp_path):
    return tmp_path / "history.json"


def _write(path, payload) -> None:
    """payload 可以是任意对象 / 原始 bytes / 字符串。

    字符串里的孤代理对按 surrogatepass 落盘（WTF-8 字节）——这本身就是
    非法 UTF-8 输入，HistoryStore.load 必须容错返回空而非抛异常。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    elif isinstance(payload, str):
        path.write_bytes(payload.encode("utf-8", "surrogatepass"))
    else:
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except UnicodeEncodeError:
            # 对象里混入孤代理对字符串：ensure_ascii 转义后仍是合法 JSON 文本
            path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")


def _random_series(rnd: random.Random) -> list:
    """随机序列：80% 非法点 / 20% 合法点（含边界）。"""
    n = rnd.choice([0, 1, 5, 50])
    out = []
    for _ in range(n):
        if rnd.random() < 0.8:
            out.append(
                rnd.choice(
                    [
                        None,
                        pick_number(rnd),
                        "not-a-point",
                        {"t": pick_number(rnd)},
                        {"p": pick_number(rnd)},
                        {"t": pick_number(rnd), "p": pick_number(rnd)},
                        {"t": float("nan"), "p": 1.0},
                        {"t": 1.0, "p": float("inf")},
                        [1, 2, 3],
                    ]
                )
            )
        else:
            out.append({"t": time.time() - rnd.random() * 86400, "p": rnd.uniform(1, 1000)})
    return out


def test_monkey_history_load_random_files(hist_path):
    """500 次随机脏文件 load：结构合法、不抛异常、坏点全部被剔除。"""
    rnd = random.Random(SEED)
    for i in range(ITERATIONS):
        roll = rnd.random()
        if i % 100 == 5:
            # 10 万点超长序列（限频 5 次，避免拖慢整体）
            big = [{"t": 1.0 + j, "p": float(j)} for j in range(100_000)]
            payload = {"gold|cny_g": big}
        elif roll < 0.1:
            payload = weird_bytes(rnd)
        elif roll < 0.2:
            payload = truncated_json(rnd)
        elif roll < 0.3:
            payload = json_with_nan()
        elif roll < 0.4:
            # 重复键 / 怪键名：gold|cny_g 出现两次（解析器取后者 null）
            junk_key = rnd.choice(["", "键\x00", "NaN"])
            junk_val = rnd.choice(["null", "[1, 2]", '{"t": "x", "p": "y"}'])
            payload = (
                '{"gold|cny_g": [{"t": 1, "p": 2}, {"t": 3, "p": 4}], '
                f'"{junk_key}": {junk_val}, "gold|cny_g": null}}'
            )
        elif roll < 0.5:
            payload = {"series": deep_nested_json(rnd.choice([100, 1000])).decode("ascii")}
        elif roll < 0.8:
            payload = dirty_value(rnd, max_depth=5)
        else:
            payload = {
                f"metal{i % 5}|unit{i % 2}": _random_series(rnd) for _ in range(rnd.randint(1, 4))
            }

        _write(hist_path, payload)

        start = time.perf_counter()
        loaded = HistoryStore(hist_path).load()
        elapsed = time.perf_counter() - start
        assert elapsed < CALL_BUDGET_S, f"第 {i} 轮 load 耗时 {elapsed:.2f}s"

        # oracle：结构必须合法（_valid_point 允许缺 t/p，按 0.0 兜底）
        assert isinstance(loaded, dict)
        for name, series in loaded.items():
            assert isinstance(name, str)
            assert isinstance(series, list) and series
            for point in series:
                assert isinstance(point, dict)
                t = float(point.get("t", 0.0))
                p = float(point.get("p", 0.0))
                assert t == t and abs(t) != float("inf")  # 有限
                assert p == p and abs(p) != float("inf")


def test_monkey_history_append_dirty_prices(hist_path):
    """500 次脏价格 append：状态文件永远保持合法 JSON，序列内全是有限点。"""
    rnd = random.Random(SEED + 1)
    store = HistoryStore(hist_path, max_points=rnd.choice([10, 100, 1440]))
    keys = [
        ("gold", "cny_g"),
        ("silver", "usd_oz"),
        ("", "\x00"),
        ("键🐳", "NaN"),
        ("a" * 200, "b"),
    ]

    for _ in range(ITERATIONS):
        metal, unit = rnd.choice(keys)
        if rnd.random() < 0.8:
            price = rnd.choice(
                [
                    float("nan"),
                    float("inf"),
                    float("-inf"),
                    10**308,
                    -(10**308),
                    -1.0,
                    0.0,
                    rnd.uniform(-1e6, 1e6),
                ]
            )
        else:
            price = rnd.uniform(100, 1000)

        start = time.perf_counter()
        series = store.append(metal, unit, price)
        elapsed = time.perf_counter() - start
        assert elapsed < CALL_BUDGET_S

        assert isinstance(series, list)
        for point in series:
            assert isinstance(point, dict)
            float(point["t"]), float(point["p"])

        if not hist_path.exists():
            # 首次记录前遇到 NaN/Inf：不落盘，序列为空
            assert series == []
            continue
        text = hist_path.read_text(encoding="utf-8")
        assert_valid_json_text(text)  # 合法 JSON 且无 NaN/Infinity 字面量
        reloaded = json.loads(text)
        assert isinstance(reloaded, dict)
        # 写盘序列与内存返回一致且不超上限；非有限价格不落盘（键可能不存在）
        stored = reloaded.get(f"{metal}|{unit}")
        price_finite = not (price != price or abs(price) == float("inf"))
        if not price_finite:
            continue
        assert isinstance(stored, list)
        assert len(stored) <= store.max_points
        if series:
            # 有限价格必须被记录（NaN/Inf 不落盘）
            assert stored[-1]["p"] == price


def test_monkey_history_load_append_roundtrip(hist_path):
    """load -> append 循环 60 轮：脏存量被清洗、文件反复重写后始终合法。"""
    store = HistoryStore(hist_path)
    _write(hist_path, {"gold|cny_g": [None, {"t": "x"}, {"t": 1, "p": 2}, {"t": 2, "p": 3}]})
    for i in range(60):
        series = store.append("gold", "cny_g", 100.0 + i + (float("nan") if i % 7 == 0 else 0))
        assert all(point["p"] == point["p"] for point in series)
        assert_valid_json_text(hist_path.read_text(encoding="utf-8"))
