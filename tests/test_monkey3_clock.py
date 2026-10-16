"""Monkey 测试 R3 目标 5：时钟异常（±1 天 / ±1 年 / 分钟级抖动 / 跨年）。

种子：20261025。
攻击面：把 time.time 注入为「前跳/后跳」的时钟，跑全部时间敏感链路：

  - HistoryStore.append 的「同分钟去重」时间轴；
  - Notifier 的涨跌幅档位/防抖、当日新高水位跨日重置、到价状态机；
  - filter_series / spark_cutoff 的 1h / today / all 时间轴；
  - CareScheduler 的 due/mark/defer 排期；
  - 勿扰时段 in_quiet_hours 的跨零点语义。

oracle：不崩、不乱（结构合法：所有 at/t/p 有限；水位只在跨日时重置；
过滤结果与解析出的 cutoff 一致）；单轮 ≤2s。
"""

from __future__ import annotations

import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from gold_pet.care import CareScheduler
from gold_pet.config import Config
from gold_pet.notifier import (
    Alert,
    Notifier,
    filter_series,
    in_quiet_hours,
    spark_cutoff,
)
from gold_pet.price import HistoryStore, Quote

SEED = 20261025
ITERATIONS = 500

BASE = 1_790_000_000.0  # 2026-09 附近的固定基准
DAY = 86400.0
YEAR = 365 * DAY

JUMPS = [
    0.0,
    DAY,
    -DAY,
    YEAR,
    -YEAR,
    2 * 3600,
    -2 * 3600,
    30 * DAY,
    -30 * DAY,
    59.0,
    -59.0,
    61.0,
    -61.0,
    6 * 3600,
    -6 * 3600,
]


def _quote(price: float = 900.0, prev: float = 890.0) -> Quote:
    return Quote(
        code="x",
        name="黄金",
        price=price,
        prev_close=prev,
        high=price,
        low=price,
        unit="元/克",
        currency="CNY",
    )


class _Clock:
    """可调时钟：monkeypatch time.time 后由测试改变 now。"""

    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock(monkeypatch):
    c = _Clock(BASE)
    monkeypatch.setattr(time, "time", c)
    return c


# ------------------------------------------------------------------ History
def test_r3_clock_history_append_jump_burst(tmp_path, clock):
    """500 次随机时钟跳变下的 append：时间轴不崩、文件严格 JSON、点数有界。"""
    rnd = random.Random(SEED)
    store = HistoryStore(tmp_path / "history.json", max_points=50)
    clock.now = BASE
    for i in range(ITERATIONS):
        clock.now += rnd.choice(JUMPS) + rnd.uniform(-120, 120)
        series = store.append("gold", "cny_g", 900.0 + i)
        assert isinstance(series, list) and len(series) <= 50
        for pt in series:
            assert math.isfinite(pt["t"]) and math.isfinite(pt["p"])
    from _monkey_common import assert_valid_json_text

    assert_valid_json_text((tmp_path / "history.json").read_text(encoding="utf-8"))


def test_r3_clock_history_dedupe_backward_jump(tmp_path, clock):
    """后跳任意幅度：<60s 差值恒为负 → 替换最后一点（不追加倒序点）；前跳 ≥60s 追加。"""
    store = HistoryStore(tmp_path / "history.json")
    clock.now = BASE
    store.append("gold", "cny_g", 900.0)
    clock.now = BASE - 30
    series = store.append("gold", "cny_g", 901.0)  # 后跳 30s：替换最后一点
    assert len(series) == 1 and series[-1]["p"] == 901.0
    clock.now = BASE - 3600
    series = store.append("gold", "cny_g", 902.0)  # 后跳 1h：仍替换（时间轴不倒退成多点）
    assert len(series) == 1 and series[-1]["p"] == 902.0
    clock.now = BASE + 120
    series = store.append("gold", "cny_g", 903.0)  # 时钟追回并前跳：正常追加
    assert len(series) == 2
    assert store.load()["gold|cny_g"][-1]["p"] == 903.0


# ------------------------------------------------------------------ Notifier
def test_r3_clock_notifier_evaluate_jump_burst(tmp_path, clock):
    """500 次时钟跳变 × 随机行情：evaluate 不崩、at 有限、水位只在跨日重置。"""
    rnd = random.Random(SEED + 1)
    cfg = Config()
    cfg.set("alert_pct", 0.5)
    cfg.set("alert_extreme_step_pct", 0.0)
    cfg.set("target_high", 0.0)
    cfg.set("target_low", 0.0)
    n = Notifier(cfg, tmp_path / "alert_history.json")
    clock.now = BASE
    alerts_seen = 0
    for _ in range(ITERATIONS):
        clock.now += rnd.choice(JUMPS) + rnd.uniform(-600, 600)
        price = 800.0 + rnd.uniform(0, 300)
        q = _quote(price=price, prev=800.0 + rnd.uniform(0, 300))
        alerts = n.evaluate(q, "黄金", base_quote=q, now=clock.now)
        for a in alerts:
            assert math.isfinite(a.at)
            assert a.kind in ("pct", "target", "extreme")
        alerts_seen += len(alerts)
    assert alerts_seen > 0  # 语料里必然有越线行情，链路真的在工作


def test_r3_clock_extreme_water_resets_across_year(clock):
    """跨年瞬间水位重置：2026-12-31 23:59 → 2027-01-01 00:01 同价可再次提醒。"""
    cfg = Config()
    cfg.set("alert_extreme_step_pct", 0.0)
    n = Notifier(cfg, None)
    t1 = time.mktime((2026, 12, 31, 23, 59, 30, 0, 0, -1))
    t2 = time.mktime((2027, 1, 1, 0, 1, 0, 0, 0, -1))
    q = _quote(price=1000.0)
    first = n.evaluate(q, "黄金", base_quote=q, now=t1)
    assert any(a.kind == "extreme" for a in first)
    same_day = n.evaluate(q, "黄金", base_quote=q, now=t1 + 20)  # 23:59:50 仍是同一天
    assert not any(a.kind == "extreme" for a in same_day)  # 同日不重复
    next_year = n.evaluate(q, "黄金", base_quote=q, now=t2)
    assert any(a.kind == "extreme" for a in next_year)  # 跨日重置后可再报


def test_r3_clock_forward_year_jump_no_storm(clock):
    """+1 年跳变：勿扰判定与提醒链路不崩；档位不因时间倒流卡死。"""
    cfg = Config()
    n = Notifier(cfg, None)
    q = _quote(price=900.0, prev=890.0)
    t = BASE
    n.evaluate(q, "黄金", base_quote=q, now=t)
    clock.now = t + YEAR
    # 大前跳后：pct 档位/防抖仍可判定（不应抛 OverflowError/ValueError）
    n.evaluate(q, "黄金", base_quote=q, now=clock.now)
    clock.now = t - YEAR
    n.evaluate(q, "黄金", base_quote=q, now=clock.now)


def test_r3_clock_dnd_across_midnight(clock):
    """勿扰跨零点：23:00-08:00 在 23:30/07:59 为真，08:00/22:59 为假。"""
    assert in_quiet_hours(23 * 60 + 30, "23:00", "08:00")
    assert in_quiet_hours(7 * 60 + 59, "23:00", "08:00")
    assert not in_quiet_hours(8 * 60, "23:00", "08:00")
    assert not in_quiet_hours(22 * 60 + 59, "23:00", "08:00")
    cfg = Config()
    cfg.set("dnd_enabled", True)
    n = Notifier(cfg, None)
    assert n.in_quiet_hours(23 * 60 + 30) is True
    assert n.in_quiet_hours(12 * 60) is False


# ------------------------------------------------------------------ filter_series
def test_r3_clock_filter_series_cutoffs(clock):
    """1h / today / all 在 ±1 天、±1 年跳变下的 cutoff 与结果一致。"""
    for now in (BASE, BASE + DAY, BASE - DAY, BASE + YEAR, BASE - YEAR):
        s = [{"t": now - 7200, "p": 1.0}, {"t": now - 60, "p": 2.0}, {"t": now + 60, "p": 3.0}]
        got_1h = filter_series(s, "1h", now=now)
        assert got_1h == [2.0, 3.0]
        got_all = filter_series(s, "all", now=now)
        assert got_all == [1.0, 2.0, 3.0]
        cutoff = spark_cutoff("today", now=now)
        assert cutoff is not None
        at_midnight = [{"t": cutoff - 1, "p": 9.0}, {"t": cutoff, "p": 8.0}]
        assert filter_series(at_midnight, "today", now=now) == [8.0]


def test_r3_clock_filter_series_random_burst(clock):
    """500 次随机时钟跳变：filter_series 输出恒为有限 float。"""
    rnd = random.Random(SEED + 2)
    now = BASE
    for _ in range(ITERATIONS):
        now += rnd.choice(JUMPS)
        series = [
            {"t": now + rnd.uniform(-2 * DAY, 2 * DAY), "p": rnd.uniform(-1e6, 1e6)}
            for _ in range(rnd.randint(0, 12))
        ]
        for rng in ("1h", "today", "all"):
            out = filter_series(series, rng, now=now)
            assert all(isinstance(v, float) and math.isfinite(v) for v in out)


# ------------------------------------------------------------------ Care
def test_r3_clock_care_scheduler_burst(clock):
    """500 次跳变下 due/mark/defer：排期不崩；前跳必到期、后跳不到期。"""
    rnd = random.Random(SEED + 3)
    cfg = Config()
    for key in ("care_drink_enabled", "care_posture_enabled", "care_eye_enabled"):
        cfg.set(key, True)
    cfg.set("care_drink_interval_min", 30)
    cfg.set("care_posture_interval_min", 45)
    cfg.set("care_eye_interval_min", 60)
    s = CareScheduler(cfg, BASE)
    for _ in range(ITERATIONS):
        jump = rnd.choice(JUMPS)
        now = clock.now + jump
        clock.now = now
        kind = s.due(now, dnd_active=False)
        assert kind in (None, "care_drink", "care_posture", "care_eye")
        if kind is not None:
            s.mark(kind, now)
            for other in ("care_drink", "care_posture", "care_eye"):
                s.defer(other, now)
        # 不变量：排期时刻恒为有限值
        for at in s._next.values():
            assert math.isfinite(at)
    # 新调度器前跳 1 年必到期；后跳 1 年（早于所有排期）不到期
    fresh = CareScheduler(cfg, BASE)
    assert fresh.due(BASE + YEAR) is not None
    assert fresh.due(BASE - YEAR) is None


# ------------------------------------------------------------------ 历史排序/提醒
def test_r3_clock_alert_sort_with_jump_timestamps(tmp_path):
    """历史里混入 ±1 年 at：倒序排序不崩，且最前者是最大时间戳。"""
    n = Notifier(Config(), tmp_path / "alert_history.json")
    ts = [BASE - YEAR, BASE + YEAR, BASE, BASE + DAY, BASE - DAY]
    n.record([Alert(kind="pct", side="up", title="t", body=str(t), at=t) for t in ts])
    items = n.history()
    assert items[0].at == BASE + YEAR
    assert items[-1].at == BASE - YEAR
