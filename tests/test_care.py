"""关怀提醒调度器（CareScheduler）的测试。"""

from __future__ import annotations

from gold_pet.care import CARE_KINDS, CareScheduler
from gold_pet.config import Config


def make_cfg(**overrides) -> Config:
    cfg = Config()
    for key, value in overrides.items():
        cfg.set(key, value)
    return cfg


def test_drink_enabled_by_default_and_due_after_interval():
    """喝水提醒默认开：60 分钟后到点。"""
    n = CareScheduler(make_cfg(), now=0.0)
    assert n.due(30 * 60) is None  # 30 分钟：没到
    assert n.due(59.9 * 60) is None
    assert n.due(60 * 60) == "care_drink"  # 60 分钟整：到点


def test_mark_schedules_next_round():
    n = CareScheduler(make_cfg(), now=0.0)
    n.mark("care_drink", now=60 * 60)
    assert n.due(60 * 60 + 1) is None
    assert n.due(60 * 60 + 60 * 60) == "care_drink"  # 再过一个周期才响


def test_disabled_kind_never_due():
    """久坐/护眼默认关，喝水关闭后也不该响。"""
    n = CareScheduler(make_cfg(care_drink_enabled=False), now=0.0)
    for t in (10, 60 * 60, 24 * 3600):
        assert n.due(t) is None, f"t={t} 不该触发（喝水已关）"


def test_priority_when_multiple_due():
    """多个同时到期：先喝水（优先级最高），其余顺延不丢。"""
    n = CareScheduler(
        make_cfg(
            care_drink_enabled=True,
            care_drink_interval_min=30,
            care_posture_enabled=True,
            care_posture_interval_min=30,
            care_eye_enabled=True,
            care_eye_interval_min=30,
        ),
        now=0.0,
    )
    assert n.due(31 * 60) == "care_drink"
    n.mark("care_drink", 31 * 60)
    # 久坐、护眼同时到期：先久坐，护眼被顺延一小段后还能响
    assert n.due(31 * 60 + 1) == "care_posture"
    n.mark("care_posture", 31 * 60 + 1)
    n.defer("care_eye", 31 * 60 + 1)
    assert n.due(31 * 60 + 2) is None  # 顺延中
    assert n.due(31 * 60 + 95) == "care_eye"


def test_dnd_silences_but_keeps_schedule():
    """勿扰期内不出声；勿扰结束后到点的提醒立即补上。"""
    n = CareScheduler(make_cfg(care_drink_interval_min=30), now=0.0)
    assert n.due(31 * 60, dnd_active=True) is None  # 勿扰：闭嘴
    assert n.due(31 * 60, dnd_active=False) == "care_drink"  # 勿扰结束：补上


def test_reset_rearranges_by_new_config():
    """设置变更后 reset：新开关/新间隔立即生效。"""
    cfg = make_cfg(care_drink_interval_min=30)
    n = CareScheduler(cfg, now=0.0)
    assert n.due(31 * 60) == "care_drink"
    cfg.set("care_drink_interval_min", 120)
    cfg.set("care_drink_enabled", False)
    n.reset(now=100.0)
    assert n.due(10_000.0) is None


def test_all_kinds_have_lines_in_ui():
    """每个关怀 kind 在 ui.LINES 里都有台词组，且不少于 6 条。"""
    from gold_pet.ui import LINES

    for kind in CARE_KINDS:
        assert kind in LINES, f"{kind} 缺少台词组"
        assert len(LINES[kind]) >= 6, f"{kind} 台词太少（{len(LINES[kind])} 条）"
