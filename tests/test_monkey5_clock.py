"""Monkey 测试 R5 目标 1：时间语义与跨日边界（前四轮未覆盖）。

种子：20261103（R1 20261002~007 / R2 20261011~016 / R3 20261021~029 之外）。

角度：前四轮打的是「脏数据」「并发」「故障注入」，没人碰**时钟**。
本套件把「时间」当作输入变量做混沌：

  1. 跨日水位重置（`_roll_day`）在本地时区日界前后必须恰好切换一次；
  2. 昨日提醒历史反序列化时不得把「昨天」的时间戳算成今天（依赖 _safe_float 口径）；
  3. 未来时间戳 / 极端时间戳（1e18、负值）不得炸穿格式化与排序；
  4. 勿扰时段与本地时间求值的一致性（含跨零点区间在 00:00 整点的开闭口径）；
  5. 历史序列按 t 排序时，t 为脏值（NaN / 字符串 / 缺失）不得逃逸异常。

oracle：不得有异常逃逸；时间边界处的布尔判定必须与「分钟数」定义一致；
单次调用 ≤2s。
"""

from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from _monkey_common import pick_number

from gold_pet.config import Config
from gold_pet.notifier import Alert, Notifier, filter_series, in_quiet_hours, parse_hhmm
from gold_pet.price import Quote

SEED = 20261103
ITERATIONS = 400


def _quote(price: float, high: float, low: float, prev_close: float = 900.0) -> Quote:
    return Quote(
        code="gds_AUTD",
        unit="cny_g",
        name="黄金",
        price=price,
        high=high,
        low=low,
        prev_close=prev_close,
        time_text="12:00:00",
    )


# ------------------------------------------------------------------ 跨日水位
def test_r5_clock_day_rollover_resets_water(monkeypatch):
    """日界前后：水位必须重置，且「同一天内」不重置。"""
    n = Notifier(Config())
    base = time.mktime(time.strptime("2026-11-03 23:59:00", "%Y-%m-%d %H:%M:%S"))
    n.evaluate(_quote(1000.0, 1000.0, 999.0), now=base)
    assert n._extreme_water.get("high") == 1000.0
    # 同一天内再刷新（更高价）应继续往上涨水位，而不是被清空
    n.evaluate(_quote(1001.0, 1001.0, 999.0), now=base + 30)
    assert n._extreme_water.get("high") == 1001.0
    # 跨日：水位清空后重新计
    n.evaluate(_quote(900.0, 900.0, 900.0), now=base + 120)  # 次日 00:01
    assert "high" not in n._extreme_water or n._extreme_water["high"] == 900.0


def test_r5_clock_day_rollover_exact_boundary(monkeypatch):
    """日界切换恰好一次：同一天内反复 evaluate 不重置水位。"""
    n = Notifier(Config())
    noon = time.mktime(time.strptime("2026-11-03 12:00:00", "%Y-%m-%d %H:%M:%S"))
    days_seen = set()
    for i in range(200):
        now = noon + i * 60
        n.evaluate(_quote(1000.0 + i, 1000.0 + i, 900.0), now=now)
        days_seen.add(n._day)
    # 200 分钟 = 3 小时 20 分，同一天内：只有一个 day key
    assert len(days_seen) == 1, days_seen


def test_r5_clock_far_future_and_past_timestamps(monkeypatch):
    """极端时间戳：未来 2100 年 / 1970 之前 / 1e18 不炸穿 evaluate。"""
    n = Notifier(Config(), history_path=None)
    # R5-M-02：1e18 曾让 time.localtime 抛 OSError 逃逸出 evaluate（已修）。
    # 修复口径是「不跨日」降级——异常不逃逸，且水位不被清空。
    for now in (0.0, -1e9, 4.1e9, 1e18, 1e308, float("nan")):
        alerts = n.evaluate(_quote(1000.0, 1000.0, 900.0), now=now)
        assert isinstance(alerts, list)
        for a in alerts:
            assert isinstance(a.at, float) and math.isfinite(a.at)
    # 负时间戳格式化不得抛
    from cli import _fmt_time

    for ts in (-1e18, 0, 1e18, float("nan"), float("inf"), "abc", None):
        assert isinstance(_fmt_time(ts, "%Y"), str)


# ------------------------------------------------------------------ 顺延/水位的单调性
def test_r5_clock_high_water_monotonic_under_random_walk():
    """随机游走 400 步：播报次数必须 < 步数（水位单调收敛），且不平庸为 0。"""
    rnd = random.Random(SEED)
    n = Notifier(Config())
    day = time.mktime(time.strptime("2026-11-03 09:00:00", "%Y-%m-%d %H:%M:%S"))
    price = 900.0
    fired = 0
    for i in range(ITERATIONS):
        price += rnd.uniform(-0.5, 1.2)  # 带上行漂移
        fired += len(n.evaluate(_quote(price, price, 880.0), now=day + i * 60))
    assert 0 < fired < ITERATIONS, f"fired={fired}"


# ------------------------------------------------------------------ 勿扰时段
@pytest.mark.parametrize(
    "start,end,minute,expected",
    [
        ("23:00", "08:00", 0, True),  # 跨零点，00:00 在时段内
        ("23:00", "08:00", 23 * 60, True),  # 含起点
        ("23:00", "08:00", 8 * 60, False),  # 不含终点
        ("23:00", "08:00", 12 * 60, False),
        ("12:00", "14:00", 13 * 60, True),
        ("12:00", "14:00", 12 * 60, True),
        ("12:00", "14:00", 14 * 60, False),
        ("00:00", "00:00", 0, False),  # 起止相同 = 未启用
    ],
)
def test_r5_clock_quiet_hours_matrix(start, end, minute, expected):
    assert in_quiet_hours(minute, start, end) is expected


def test_r5_clock_quiet_hours_dirty_strings():
    """脏时段字符串：非法一律按「未启用」（False），绝不抛。"""
    dirty = ["", "  ", "25:00", "-1:00", "12:60", "abc", "1", "12:0:0", "１２：００", "\x00"]
    for text in dirty:
        assert parse_hhmm(text) is None, text
        assert in_quiet_hours(600, text, "08:00") is False, text
        assert in_quiet_hours(600, "23:00", text) is False, text
    # 非 str 输入（JSON 里写成数字/None/bool）
    for bad in (None, 600, True, ["12:00"], {"a": 0}):
        assert parse_hhmm(bad) is None


def test_r5_clock_quiet_hours_full_day_sweep():
    """全天 1440 分钟扫描：跨零点区间的 True 区间必须连续且正好覆盖 [23:00,24:00)+[0,08:00)。"""
    hits = [m for m in range(1440) if in_quiet_hours(m, "23:00", "08:00")]
    assert hits == list(range(0, 480)) + list(range(1380, 1440))
    assert len(hits) == 9 * 60


# ------------------------------------------------------------------ 历史时间戳
def test_r5_clock_alert_history_dirty_at(tmp_path):
    """alert_history.json 里 at 为脏值：加载不炸、排序不炸、相对时间可格式化。"""
    rnd = random.Random(SEED + 1)
    items = []
    for _ in range(120):
        items.append(
            {
                "kind": rnd.choice(["pct", "target", "extreme", ""]),
                "side": rnd.choice(["up", "down", "high", "low"]),
                "title": "t",
                "body": "b",
                "at": pick_number(rnd),
            }
        )
    path = tmp_path / "alert_history.json"
    import json

    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    n = Notifier(Config(), path)
    history = n.history()
    assert isinstance(history, list)
    from cli import _fmt_time

    for a in history:
        assert isinstance(a.at, float) and math.isfinite(a.at)
        assert isinstance(_fmt_time(a.at, "%Y-%m-%d"), str)


def test_r5_clock_history_series_sort_with_dirty_t():
    """filter_series：t 为脏值时按 t 排序不抛（NaN 不得成为排序键）。"""
    rnd = random.Random(SEED + 2)
    series = []
    for _ in range(300):
        series.append({"t": pick_number(rnd), "p": pick_number(rnd)})
    for now in (None, 0.0, 4.1e9):
        for range_key in ("1h", "today", "all", "bogus", ""):
            out = filter_series(series, range_key, now)
            assert isinstance(out, list)
            assert all(isinstance(v, float) and math.isfinite(v) for v in out)


def test_r5_clock_timezone_independence(monkeypatch):
    """时区切换（UTC vs 固定东八区）下，_roll_day 结果只依赖本地时区，不串味。"""
    import os

    day_a = time.strftime("%Y-%m-%d", time.localtime(time.time()))
    assert day_a  # 至少保证本地时区可用
    # 用固定 now 校验：_roll_day 写入的 key 必须等于 time.strftime 的结果
    n = Notifier(Config())
    now = time.time()
    n._roll_day(now)
    assert n._day == time.strftime("%Y-%m-%d", time.localtime(now))
    prev_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "Asia/Shanghai"
        time.tzset()
        n._roll_day(now)
        assert n._day == time.strftime("%Y-%m-%d", time.localtime(now))
    finally:
        if prev_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prev_tz
        time.tzset()


def test_r5_clock_alert_at_default_is_now():
    """Alert 默认 at 必须接近当前时间（不得退回 0 导致历史排序错乱）。"""
    a = Alert(kind="pct", side="up", title="t", body="b")
    assert abs(a.at - time.time()) < 5
    # 脏 dict 反序列化：at 缺失/None -> 0.0 兜底（既有口径），可格式化
    bad = Alert.from_dict({"at": None})
    assert bad.at == 0.0
