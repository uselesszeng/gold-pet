"""提醒规则、勿扰判断与走势范围过滤的单元测试。

notifier.py 是 v0.5 抽出的纯逻辑层，不依赖 Qt，因此可以精确断言规则边界。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gold_pet.config import Config  # noqa: E402
from gold_pet.notifier import (  # noqa: E402
    KIND_EXTREME,
    KIND_PCT,
    KIND_TARGET,
    Alert,
    Notifier,
    filter_series,
    format_hhmm,
    in_quiet_hours,
    parse_hhmm,
    spark_cutoff,
)
from gold_pet.price import Quote  # noqa: E402


def make_quote(price=900.0, prev=900.0, high=0.0, low=0.0, unit="元/克") -> Quote:
    return Quote(
        code="X",
        name="测试",
        price=price,
        prev_close=prev,
        high=high,
        low=low,
        unit=unit,
    )


def make_notifier(cfg=None, path=None) -> Notifier:
    return Notifier(cfg or Config(), path)


# ------------------------------------------------------------------ HH:MM 解析


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("00:00", 0),
        ("08:30", 8 * 60 + 30),
        ("23:59", 23 * 60 + 59),
        ("9:05", 9 * 60 + 5),
        ("24:00", None),
        ("12:60", None),
        ("abc", None),
        ("12", None),
        ("", None),
        ("12:34:56", None),
    ],
)
def test_parse_hhmm(text, expected):
    assert parse_hhmm(text) == expected


def test_format_hhmm():
    assert format_hhmm(0) == "00:00"
    assert format_hhmm(8 * 60 + 5) == "08:05"
    assert format_hhmm(23 * 60 + 59) == "23:59"


# ------------------------------------------------------------------ 勿扰时段


@pytest.mark.parametrize(
    ("now", "start", "end", "expected", "desc"),
    [
        (23 * 60 + 30, "23:00", "08:00", True, "跨零点：23:30 在内"),
        (7 * 60, "23:00", "08:00", True, "跨零点：07:00 在内"),
        (8 * 60, "23:00", "08:00", False, "跨零点：08:00 结束边界（不含）"),
        (12 * 60, "23:00", "08:00", False, "跨零点：12:00 在外"),
        (13 * 60, "12:00", "14:00", True, "同日区间：13:00 在内"),
        (12 * 60, "12:00", "14:00", True, "同日区间：起始含"),
        (14 * 60, "12:00", "14:00", False, "同日区间：结束不含"),
        (11 * 60, "12:00", "14:00", False, "同日区间：11:00 在外"),
        (12 * 60, "12:00", "12:00", False, "起止相同 → 视为未启用"),
        (12 * 60, "", "", False, "空配置 → 视为未启用"),
        (12 * 60, "坏值", "08:00", False, "非法起 → 未启用"),
    ],
)
def test_in_quiet_hours(now, start, end, expected, desc):
    assert in_quiet_hours(now, start, end) is expected, desc


def test_notifier_quiet_hours_respects_switch():
    cfg = Config()
    cfg.set("dnd_enabled", False)
    cfg.set("dnd_start", "00:00")
    cfg.set("dnd_end", "23:59")
    assert Notifier(cfg).in_quiet_hours(12 * 60) is False

    cfg.set("dnd_enabled", True)
    assert Notifier(cfg).in_quiet_hours(12 * 60) is True


# ------------------------------------------------------------------ 涨跌幅提醒


def test_pct_alert_triggers_and_never_repeats_on_flat_price():
    """v0.5.6 核心回归：涨跌幅不变时不再随时间反复弹窗。

    旧逻辑只按「类型 + 方向」去重，冷却 600 秒一到就再弹一次。真实用户日志里
    金价锁死 906.80、涨跌幅恒为 +1.14%，每 10 分钟弹一次同一句话。
    """
    cfg = Config()
    cfg.set("alert_pct", 0.5)
    cfg.set("alert_cooldown_sec", 600)
    n = Notifier(cfg)
    q = make_quote(price=905.0, prev=900.0)  # +0.56% → 1 档

    alerts = n.evaluate(q, "黄金", now=1000.0)
    assert [a.kind for a in alerts] == [KIND_PCT]
    assert alerts[0].side == "up"

    # 同一涨跌幅：冷却期内不重复，冷却期外也不重复
    assert n.evaluate(q, "黄金", now=1100.0) == []
    assert n.evaluate(q, "黄金", now=50000.0) == []  # 同一天内的很久以后


def test_pct_alert_fires_again_only_on_higher_step():
    """只有跨入更高档位（阈值整数倍）才再提醒一次。"""
    cfg = Config()
    cfg.set("alert_pct", 0.5)
    n = Notifier(cfg)

    # +0.67% → 1 档
    q1 = make_quote(price=906.0, prev=900.0)
    assert [a.kind for a in n.evaluate(q1, "黄金", now=1.0)] == [KIND_PCT]
    assert n.evaluate(q1, "黄金", now=2.0) == []

    # +1.14% → 2 档：跨档，提醒
    q2 = make_quote(price=910.26, prev=900.0)
    assert [a.kind for a in n.evaluate(q2, "黄金", now=3.0)] == [KIND_PCT]
    assert n.evaluate(q2, "黄金", now=4.0) == []

    # +1.60% → 3 档：继续跨档，提醒
    q3 = make_quote(price=914.4, prev=900.0)
    assert [a.kind for a in n.evaluate(q3, "黄金", now=5.0)] == [KIND_PCT]

    # 回落到 2 档：没有创出更高的涨幅，不提醒
    assert n.evaluate(q2, "黄金", now=6.0) == []


def test_pct_alert_rearms_after_leaving_threshold():
    """跌破阈值后重新武装；抖动由防抖间隔拦下。"""
    cfg = Config()
    cfg.set("alert_pct", 0.5)
    cfg.set("alert_cooldown_sec", 600)
    n = Notifier(cfg)
    hot = make_quote(price=906.0, prev=900.0)  # +0.67%
    flat = make_quote(price=901.0, prev=900.0)  # +0.11%，回到阈值内

    assert [a.kind for a in n.evaluate(hot, "黄金", now=1000.0)] == [KIND_PCT]
    assert n.evaluate(flat, "黄金", now=1010.0) == []
    # 抖动：防抖期内又越线，不提醒
    assert n.evaluate(hot, "黄金", now=1020.0) == []
    # 超过防抖间隔后再次越线：重新提醒
    assert [a.kind for a in n.evaluate(hot, "黄金", now=2000.0)] == [KIND_PCT]


def test_pct_alert_fires_on_direction_reversal():
    """同样 1 档但方向反转属于新信息，应立刻提醒。"""
    cfg = Config()
    cfg.set("alert_pct", 0.5)
    n = Notifier(cfg)
    up = make_quote(price=906.0, prev=900.0)  # +0.67%
    down = make_quote(price=894.0, prev=900.0)  # -0.67%

    assert [a.side for a in n.evaluate(up, "黄金", now=1.0)] == ["up"]
    assert [a.side for a in n.evaluate(up, "黄金", now=2.0)] == []
    assert [a.side for a in n.evaluate(down, "黄金", now=3.0)] == ["down"]


def test_reset_state_lets_alerts_fire_again():
    """reset_state() 后同一状态可以重新提醒（换品种 / 改阈值 / 清空历史时调用）。"""
    cfg = Config()
    cfg.set("alert_pct", 0.5)
    n = Notifier(cfg)
    q = make_quote(price=906.0, prev=900.0)

    assert [a.kind for a in n.evaluate(q, "黄金", now=1.0)] == [KIND_PCT]
    assert n.evaluate(q, "黄金", now=2.0) == []
    n.reset_state()
    assert [a.kind for a in n.evaluate(q, "黄金", now=3.0)] == [KIND_PCT]


def test_pct_alert_below_threshold_no_trigger():
    cfg = Config()
    cfg.set("alert_pct", 5.0)
    n = Notifier(cfg)
    assert n.evaluate(make_quote(price=901.0, prev=900.0), "黄金", now=1.0) == []


def test_pct_alert_disabled():
    cfg = Config()
    cfg.set("alert_pct", 0.5)
    cfg.set("alert_pct_enabled", False)
    n = Notifier(cfg)
    assert n.evaluate(make_quote(price=920.0, prev=900.0), "黄金", now=1.0) == []


def test_pct_alert_down_side():
    cfg = Config()
    cfg.set("alert_pct", 0.5)
    n = Notifier(cfg)
    alerts = n.evaluate(make_quote(price=880.0, prev=900.0), "黄金", now=1.0)
    assert alerts[0].side == "down"
    assert "下跌" in alerts[0].body


# ------------------------------------------------------------------ 新高新低


def test_extreme_high_alert():
    cfg = Config()
    n = Notifier(cfg)
    q = make_quote(price=950.0, prev=900.0, high=950.0, low=880.0)
    alerts = n.evaluate(q, "黄金", now=1.0)
    assert any(a.kind == KIND_EXTREME and a.side == "high" for a in alerts)
    assert "创新高" in next(a for a in alerts if a.kind == KIND_EXTREME).body


def test_extreme_low_alert():
    cfg = Config()
    n = Notifier(cfg)
    q = make_quote(price=880.0, prev=900.0, high=950.0, low=880.0)
    alerts = n.evaluate(q, "黄金", now=1.0)
    assert any(a.kind == KIND_EXTREME and a.side == "low" for a in alerts)


def test_extreme_disabled():
    cfg = Config()
    cfg.set("alert_extreme_enabled", False)
    n = Notifier(cfg)
    q = make_quote(price=950.0, prev=900.0, high=950.0, low=880.0)
    assert not any(a.kind == KIND_EXTREME for a in n.evaluate(q, "黄金", now=1.0))


def test_extreme_ignored_without_high_low():
    n = Notifier(Config())
    q = make_quote(price=900.0, prev=900.0, high=0.0, low=0.0)
    assert not any(a.kind == KIND_EXTREME for a in n.evaluate(q, "黄金", now=1.0))


def test_extreme_high_never_repeats_while_price_flat():
    """v0.5.6 核心回归：现价贴在当日最高价横盘时不再反复弹「创新高」。

    行情源的当日最高价本身就被现价顶着，`price >= high` 恒成立，旧逻辑因此
    每过冷却期就重弹一次。现在要求严格越过上次播报的水位。
    """
    cfg = Config()
    n = Notifier(cfg)
    q = make_quote(price=907.6, prev=900.0, high=907.6, low=896.33)
    # 用明确的同一天基准时间，避免时间戳跨日触发水位重置
    base = time.mktime((2026, 9, 30, 9, 0, 0, 0, 0, -1))

    assert any(a.kind == KIND_EXTREME for a in n.evaluate(q, "黄金", now=base))
    # 价格一动不动：当天内无论隔多久都不该再弹
    assert n.evaluate(q, "黄金", now=base + 60) == []
    assert n.evaluate(q, "黄金", now=base + 8 * 3600) == []


def test_extreme_high_requires_strictly_new_watermark():
    """只有严格刷新水位才算新高：持平不报、回落不报、真突破才报。"""
    cfg = Config()
    cfg.set("alert_extreme_step_pct", 0.0)  # 本用例只验证水位，先关掉步进
    n = Notifier(cfg)
    # (现价, 当日最高, 是否应提醒)
    stages = [
        (907.6, 907.6, True),  # 首次：播报并记下水位
        (907.6, 907.6, False),  # 持平：内容相同，不重复
        (906.0, 907.6, False),  # 回落：现价低于今高，不判定
        (907.0, 907.6, False),  # 仍未越过水位
        (908.1, 908.1, True),  # 真突破：提醒
        (908.1, 908.1, False),  # 又横盘：不重复
    ]
    for idx, (price, high, expected) in enumerate(stages):
        q = make_quote(price=price, prev=900.0, high=high, low=896.33)
        fired = any(a.kind == KIND_EXTREME for a in n.evaluate(q, "黄金", now=1000.0 + idx))
        assert fired is expected, f"第 {idx} 步（现价 {price} / 今高 {high}）期望 {expected}"


def test_extreme_low_requires_strictly_new_watermark():
    cfg = Config()
    cfg.set("alert_extreme_step_pct", 0.0)  # 本用例只验证水位
    n = Notifier(cfg)
    first = make_quote(price=896.33, prev=900.0, high=907.6, low=896.33)
    assert any(a.kind == KIND_EXTREME and a.side == "low" for a in n.evaluate(first, "黄金", now=1.0))
    assert n.evaluate(first, "黄金", now=2.0) == []

    deeper = make_quote(price=895.0, prev=900.0, high=907.6, low=895.0)
    alerts = n.evaluate(deeper, "黄金", now=3.0)
    assert any(a.kind == KIND_EXTREME and a.side == "low" for a in alerts)
    assert n.evaluate(deeper, "黄金", now=4.0) == []


def test_extreme_step_damps_micro_highs():
    """v0.5.6：0.1% 步进把连续微创新高收敛成一次提醒。

    真实用户日志里 15:21~15:23 连刷 907.70 / 907.96 / 908.07，只按水位判定会连弹
    三次；加上步进后只报第一次，直到真的再涨出 0.1% 才提醒。
    """
    cfg = Config()
    cfg.set("alert_extreme_step_pct", 0.1)
    n = Notifier(cfg)
    base = time.mktime((2026, 9, 30, 9, 0, 0, 0, 0, -1))

    def fired(price, offset):
        q = make_quote(price=price, prev=900.0, high=price, low=896.33)
        return any(a.kind == KIND_EXTREME for a in n.evaluate(q, "黄金", now=base + offset))

    assert fired(907.70, 0) is True  # 首次播报并记下水位
    assert fired(907.96, 60) is False  # 只高 0.03%，不值得再提醒
    assert fired(908.07, 120) is False  # 仍未达 907.70 × 1.001 = 908.61
    assert fired(908.70, 180) is True  # 越过步进：提醒
    assert fired(908.70, 240) is False  # 又横盘：不重复


def test_extreme_step_zero_reports_every_new_high():
    """步进设为 0 时退化为「任何新高都提醒」，保留给想听全部动静的用户。"""
    cfg = Config()
    cfg.set("alert_extreme_step_pct", 0.0)
    n = Notifier(cfg)
    base = time.mktime((2026, 9, 30, 9, 0, 0, 0, 0, -1))

    for idx, price in enumerate([907.70, 907.96, 908.07]):
        q = make_quote(price=price, prev=900.0, high=price, low=896.33)
        assert any(
            a.kind == KIND_EXTREME for a in n.evaluate(q, "黄金", now=base + idx)
        ), f"现价 {price} 应提醒"


def test_extreme_watermark_resets_on_new_day():
    """跨日后水位清零：新一天的首个高点可以重新提醒。"""
    n = Notifier(Config())
    q = make_quote(price=950.0, prev=900.0, high=950.0, low=880.0)
    day1 = time.mktime((2026, 9, 30, 10, 0, 0, 0, 0, -1))
    day2 = time.mktime((2026, 10, 1, 10, 0, 0, 0, 0, -1))

    assert any(a.kind == KIND_EXTREME for a in n.evaluate(q, "黄金", now=day1))
    assert n.evaluate(q, "黄金", now=day1 + 60) == []
    # 次日水位清空，同一价位重新提醒一次
    assert any(a.kind == KIND_EXTREME for a in n.evaluate(q, "黄金", now=day2))


# ------------------------------------------------------------------ 到价提醒


def test_target_high_and_low():
    cfg = Config()
    cfg.set("target_high", 950.0)
    n = Notifier(cfg)
    alerts = n.evaluate(
        make_quote(price=960.0), "黄金", base_quote=make_quote(price=960.0), now=1.0
    )
    assert any(a.kind == KIND_TARGET and a.side == "high" for a in alerts)

    cfg2 = Config()
    cfg2.set("target_low", 880.0)
    n2 = Notifier(cfg2)
    alerts2 = n2.evaluate(
        make_quote(price=870.0), "黄金", base_quote=make_quote(price=870.0), now=1.0
    )
    assert any(a.kind == KIND_TARGET and a.side == "low" for a in alerts2)


def test_target_needs_cny_base():
    """展示单位是美元/盎司、又拿不到元/克基准时不应误判。"""
    cfg = Config()
    cfg.set("target_low", 5000.0)
    n = Notifier(cfg)
    usd_quote = make_quote(price=4100.0, unit="美元/盎司")
    assert not any(a.kind == KIND_TARGET for a in n.evaluate(usd_quote, "黄金", now=1.0))


def test_target_disabled_or_zero():
    cfg = Config()
    cfg.set("target_high", 0.0)
    cfg.set("target_low", 0.0)
    n = Notifier(cfg)
    q = make_quote(price=9999.0)
    assert not any(a.kind == KIND_TARGET for a in n.evaluate(q, "黄金", base_quote=q, now=1.0))


def test_target_disabled_switch():
    cfg = Config()
    cfg.set("target_low", 900.0)
    cfg.set("alert_target_enabled", False)
    n = Notifier(cfg)
    q = make_quote(price=800.0)
    assert not any(a.kind == KIND_TARGET for a in n.evaluate(q, "黄金", base_quote=q, now=1.0))


def test_target_fires_once_until_price_leaves_zone():
    """v0.5.6：到价提醒只在「进入目标价」那一刻报一次，横盘不重复。"""
    cfg = Config()
    cfg.set("target_high", 950.0)
    n = Notifier(cfg)
    above = make_quote(price=960.0)
    below = make_quote(price=940.0)

    def fired(quote, now):
        return any(
            a.kind == KIND_TARGET for a in n.evaluate(quote, "黄金", base_quote=quote, now=now)
        )

    assert fired(above, 1.0) is True
    # 一直在目标价之上：无论隔多久都不再提醒
    assert fired(above, 99999.0) is False
    # 回到目标价之下：重新武装（本身不提醒）
    assert fired(below, 200000.0) is False
    # 再次突破：重新提醒
    assert fired(above, 200100.0) is True


# ------------------------------------------------------------------ 历史


def test_history_record_and_reload(tmp_path):
    path = tmp_path / "alert_history.json"
    cfg = Config()
    cfg.set("alert_pct", 0.5)

    n = Notifier(cfg, path)
    alerts = n.evaluate(make_quote(price=910.0, prev=900.0), "黄金", now=100.0)
    n.record(alerts)
    assert len(n.history()) == 1

    # 新实例应从磁盘恢复
    n2 = Notifier(cfg, path)
    assert len(n2.history()) == 1
    assert n2.history()[0].kind == KIND_PCT


def test_history_caps_at_max(tmp_path):
    path = tmp_path / "alert_history.json"
    n = Notifier(Config(), path, max_history=3)
    for i in range(10):
        n.record([Alert(kind="pct", side="up", title="t", body=f"第{i}条", at=float(i))])
    hist = n.history()
    assert len(hist) == 3
    # 保留最新的 3 条，且按时间倒序
    assert hist[0].body == "第9条"


def test_history_filter_by_kind(tmp_path):
    n = Notifier(Config(), tmp_path / "h.json")
    n.record([Alert(kind="pct", side="up", title="t", body="a", at=1.0)])
    n.record([Alert(kind="target", side="low", title="t", body="b", at=2.0)])
    assert len(n.history(KIND_PCT)) == 1
    assert len(n.history()) == 2


def test_history_clear(tmp_path):
    path = tmp_path / "h.json"
    n = Notifier(Config(), path)
    n.record([Alert(kind="pct", side="up", title="t", body="a")])
    n.clear_history()
    assert n.history() == []
    # 清空应落盘
    assert json.loads(path.read_text(encoding="utf-8")) == []


def test_history_corrupted_file_recovers(tmp_path):
    path = tmp_path / "h.json"
    path.write_text("{不是列表", encoding="utf-8")
    n = Notifier(Config(), path)
    assert n.history() == []
    n.record([Alert(kind="pct", side="up", title="t", body="a")])
    assert len(n.history()) == 1


def test_history_ignores_non_dict_items(tmp_path):
    path = tmp_path / "h.json"
    path.write_text(json.dumps([1, "x", {"kind": "pct", "body": "有效"}]), encoding="utf-8")
    n = Notifier(Config(), path)
    assert len(n.history()) == 1


def test_history_without_path_is_memory_only():
    n = Notifier(Config(), None)
    n.record([Alert(kind="pct", side="up", title="t", body="a")])
    assert len(n.history()) == 1  # 不抛异常


# ------------------------------------------------------------------ 走势范围


def test_spark_cutoff():
    now = time.time()
    assert spark_cutoff("all", now) is None
    assert abs(spark_cutoff("1h", now) - (now - 3600)) < 1
    today = spark_cutoff("today", now)
    assert today is not None
    assert time.localtime(today).tm_hour == 0
    assert time.localtime(today).tm_min == 0
    assert spark_cutoff("未知", now) is None


def test_filter_series_by_range():
    now = time.time()
    series = [
        {"t": now - 7200, "p": 100.0},  # 2 小时前
        {"t": now - 1800, "p": 200.0},  # 半小时前
        {"t": now - 60, "p": 300.0},  # 1 分钟前
    ]
    assert filter_series(series, "1h", now) == [200.0, 300.0]
    assert filter_series(series, "all", now) == [100.0, 200.0, 300.0]
    assert len(filter_series(series, "today", now)) >= 1


def test_filter_series_empty_and_bad():
    assert filter_series([], "1h") == []
    # 缺字段按 0 处理，不抛异常
    assert filter_series([{"p": 5.0}], "all") == [5.0]
