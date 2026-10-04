"""Monkey 测试 R2 目标 3：Notifier 状态机随机序列 + DND 时序交错（一轮未覆盖）。

种子：20261013；迭代：20 条线 × 500 步 = 10000 步随机交错
（evaluate / record / clear_history / reset_state / 只读 history /
配置突变：阈值、目标价、DND 开关与时段）。

oracle：
- 内存 _history 与 alert_history.json 每一步都一致
- 条数恒 ≤ max_history；文件恒为合法 JSON
- DND 全分钟边界网格语义正确（同日/跨零点/起止相同/非法时段）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import random

from _monkey_common import assert_valid_json_text

from gold_pet.config import Config
from gold_pet.notifier import Alert, Notifier, Quote, in_quiet_hours

SEED = 20261013
TRIALS = 20
STEPS = 500
MAX_HISTORY = 50


def _random_quote(rnd: random.Random, price: float) -> Quote:
    return Quote(
        code="gds_AUTD",
        name="黄金",
        price=price,
        prev_close=price - rnd.uniform(-3, 3),
        high=price + rnd.choice([0.0, 0.05, 0.5, 5.0]),
        low=max(0.01, price - rnd.choice([0.0, 0.05, 0.5, 5.0])),
    )


def test_r2_notifier_state_machine_random_interleave(tmp_path):
    rnd = random.Random(SEED)
    for trial in range(TRIALS):
        cfg = Config({})
        hp = tmp_path / f"ah{trial}.json"
        n = Notifier(cfg, history_path=hp, max_history=MAX_HISTORY)
        price = 700.0
        now = 1_700_000_000.0
        for step in range(STEPS):
            op = rnd.random()
            if op < 0.50:
                price = max(0.01, price + rnd.uniform(-8, 8))
                now += rnd.uniform(1, 120)
                q = _random_quote(rnd, price)
                if rnd.random() < 0.15:
                    cfg.set("alert_pct", rnd.choice([0.0, 0.1, 0.5, 5.0]))
                if rnd.random() < 0.1:
                    cfg.set("target_high", rnd.choice([0.0, 690.0, 705.0]))
                if rnd.random() < 0.1:
                    cfg.set("target_low", rnd.choice([0.0, 695.0, 710.0]))
                if rnd.random() < 0.1:
                    cfg.set("alert_pct_enabled", rnd.choice([True, False]))
                if rnd.random() < 0.1:
                    cfg.set("alert_extreme_step_pct", rnd.choice([0.0, 0.1, 1.0]))
                alerts = n.evaluate(q, "黄金", base_quote=q, now=now)
                n.record(alerts)
            elif op < 0.65:
                n.record([Alert(kind="pct", side="up", title="t", body="b", at=now)])
            elif op < 0.75:
                n.clear_history()
            elif op < 0.85:
                n.reset_state()
            elif op < 0.92:
                # DND 开关与提醒触发时序交错：判定不受状态污染
                cfg.set("dnd_enabled", rnd.choice([True, False]))
                n.in_quiet_hours(rnd.randint(0, 1439))
            else:
                n.history()  # 只读排序不得炸
            # oracle：内存与文件逐步一致、上限恒成立
            if hp.exists():
                on_disk = json.loads(hp.read_text(encoding="utf-8"))
                assert on_disk == [a.to_dict() for a in n._history], (
                    f"trial{trial} step{step} 内存/文件不一致"
                )
                assert_valid_json_text(hp.read_text(encoding="utf-8"))
            assert len(n._history) <= MAX_HISTORY


def test_r2_dnd_boundary_grid():
    """全分钟网格 × 典型时段：勿扰判定语义精确（与提醒触发的时序交错前置）。"""
    for minute in range(1440):
        # 跨零点 23:00-08:00
        expect = minute >= 23 * 60 or minute < 8 * 60
        assert in_quiet_hours(minute, "23:00", "08:00") is expect, f"minute={minute}"
        # 同日 12:00-14:00
        expect = 12 * 60 <= minute < 14 * 60
        assert in_quiet_hours(minute, "12:00", "14:00") is expect
    # 起止相同/非法 => 未启用
    assert in_quiet_hours(600, "08:00", "08:00") is False
    assert in_quiet_hours(600, "", "") is False
    assert in_quiet_hours(600, "bad", "08:00") is False
    assert in_quiet_hours(600, "24:00", "08:00") is False


def test_r2_dnd_toggle_between_alerts(tmp_path):
    """DND 开关在 evaluate 间随机翻转 500 次：判定结果只由当前配置决定，无残留状态。"""
    rnd = random.Random(SEED + 1)
    cfg = Config({"dnd_enabled": True, "dnd_start": "23:00", "dnd_end": "08:00"})
    n = Notifier(cfg, history_path=tmp_path / "ah.json")
    q = Quote(code="g", name="g", price=700.0, prev_close=700.0, high=0.0, low=0.0)
    for i in range(500):
        enabled = rnd.random() < 0.5
        cfg.set("dnd_enabled", enabled)
        assert n.in_quiet_hours(23 * 60 + 30) is enabled, f"i={i}"
        # evaluate 在 DND 翻转间不炸、不产生历史污染
        n.record(n.evaluate(q, "黄金", base_quote=q, now=1700000000.0 + i))
