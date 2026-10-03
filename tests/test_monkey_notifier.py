"""Monkey 测试目标 5：notifier.py filter_series / Alert.from_dict / in_quiet_hours。

种子：20261005；迭代：filter_series 500 + Alert.from_dict 500 + in_quiet_hours 500
（另加 60 次 Notifier.evaluate 端到端扰动）。
语料：随机序列与参数组合，80% 非法 / 20% 合法边界（NaN/Inf/None/错类型/超大数）。

oracle：不抛异常；filter_series 返回有限 float 列表；Alert.at 为有限 float；
in_quiet_hours 返回 bool。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 项目根入 sys.path（同 test_main.py 惯例）

import math
import random
import time

from _monkey_common import dirty_value, pick_number, pick_text

from gold_pet.config import Config
from gold_pet.notifier import Alert, Notifier, filter_series, in_quiet_hours
from gold_pet.price import Quote

SEED = 20261005
ITERATIONS = 500


def _random_series(rnd: random.Random) -> list:
    n = rnd.choice([0, 1, 10, 120])
    out = []
    for _ in range(n):
        if rnd.random() < 0.8:
            out.append(rnd.choice([
                None,
                42,
                "point",
                [],
                {},
                {"t": pick_number(rnd), "p": pick_number(rnd)},
                {"p": pick_number(rnd)},
                {"t": float("nan"), "p": 5.0},
                {"t": 1.0, "p": float("nan")},
                {"t": "abc", "p": "def"},
                {"t": 1e308, "p": -1e308},
                dirty_value(rnd),
            ]))
        else:
            out.append({"t": time.time() - rnd.random() * 7200, "p": rnd.uniform(1, 1000)})
    return out


def test_monkey_filter_series():
    rnd = random.Random(SEED)
    ranges = ["1h", "today", "all", "", "bogus", None, 1, ["1h"]]
    for _ in range(ITERATIONS):
        series = _random_series(rnd)
        range_key = rnd.choice(ranges)
        # now 在生产里来自时钟；语料取当前时间的 ±1e6 秒扰动（非时钟量级的
        # 荒谬 now 属于 stdlib localtime 的行为域，不在本目标 oracle 内）
        now = None if rnd.random() < 0.2 else time.time() + rnd.uniform(-1e6, 1e6)
        values = filter_series(series, range_key, now)
        assert isinstance(values, list)
        for v in values:
            assert isinstance(v, float)
            assert math.isfinite(v), f"filter_series 输出非有限值 {v}"


def test_monkey_alert_from_dict():
    rnd = random.Random(SEED + 1)
    for _ in range(ITERATIONS):
        if rnd.random() < 0.8:
            data = dirty_value(rnd, max_depth=2)
            if not isinstance(data, dict):
                data = {"kind": data, "side": data, "title": data, "body": data,
                        "at": pick_number(rnd)}
        else:
            data = {"kind": rnd.choice(["pct", "target", "extreme", ""]),
                    "side": rnd.choice(["up", "down", "high", "low", ""]),
                    "title": pick_text(rnd), "body": pick_text(rnd),
                    "at": time.time() + rnd.uniform(-1e6, 1e6)}
        alert = Alert.from_dict(data)  # 抛任何异常即失败
        assert isinstance(alert, Alert)
        assert isinstance(alert.at, float)
        assert math.isfinite(alert.at)  # M-05：NaN/Inf 坏值按 0 兜底


def test_monkey_in_quiet_hours():
    rnd = random.Random(SEED + 2)
    times = ["00:00", "23:59", "08:00", "12:34", "", "25:00", "12:60", "nan", "ab:cd",
             "7:5", " 08:00 ", "0800", None, 480, ["08:00"], "08:00:00", "🐳:🐳", "\x00:00"]
    for _ in range(ITERATIONS):
        start = rnd.choice(times)
        end = rnd.choice(times)
        if rnd.random() < 0.2:
            now = rnd.choice([None, 0, 480, 1439, -1, 10**12])
        else:
            now = rnd.randint(0, 1439)
        result = in_quiet_hours(now, start, end)  # 抛任何异常即失败
        assert isinstance(result, bool)


def test_monkey_notifier_evaluate_with_dirty_config(tmp_path):
    """端到端扰动：脏配置 + 随机 Quote 走 Notifier.evaluate，不炸、at 有限。"""
    rnd = random.Random(SEED + 3)
    dirty_cfgs = [
        {"alert_pct": float("nan"), "alert_cooldown_sec": float("inf")},
        {"alert_pct": -5, "alert_cooldown_sec": -600, "alert_extreme_step_pct": 10**308},
        {"target_high": float("nan"), "target_low": "abc"},
        {"alert_pct": 0, "target_high": 0},
        {"metal": None, "unit": 42, "alert_pct": [1]},
    ]
    for i in range(60):
        cfg = Config(dirty_cfgs[i % len(dirty_cfgs)])
        cfg.sanitize()
        notifier = Notifier(cfg)
        price = rnd.uniform(100, 1000)
        base = price * rnd.uniform(0.9, 1.1)
        high = price * rnd.choice([1.0, 1.001, 0.999])
        low = price * rnd.choice([1.0, 0.999, 1.001])
        quote = Quote(code="t", name="n", price=price, prev_close=base,
                      high=max(high, low), low=min(high, low), unit="元/克")
        alerts = notifier.evaluate(quote, now=time.time() + i * 3600)
        assert isinstance(alerts, list)
        for a in alerts:
            assert math.isfinite(a.at)
        # 勿扰判定也不炸
        assert isinstance(notifier.in_quiet_hours(rnd.randint(0, 1439)), bool)
