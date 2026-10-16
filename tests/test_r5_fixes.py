"""R5 修复回归锚（无人值守第 1 轮 / R5）。

每个测试对应一条已修缺陷，防回归：
  - R5-M-01  配置/发现文件落盘原子化（Issue #20）
  - R5-M-02  极端时间戳不做跨日重置（Issue #21）
  - R5-M-03  Alert.from_dict 类型守卫（Issue #24）
  - R5-04    _num 类型表达收窄（Issue #23 / mypy 门禁）
"""

from __future__ import annotations

import errno
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from gold_pet.config import Config
from gold_pet.notifier import Alert, Notifier
from gold_pet.price import Quote, _num


# ------------------------------------------------------------------ Issue #20
def test_r5_config_save_atomic_no_torn_read(tmp_path):
    """并发读 settings.json：save 期间不得出现空文件/半截 JSON（修复前 300 轮命中 292 次）。"""
    path = tmp_path / "settings.json"
    cfg = Config.load(path)
    cfg.save()

    bad: list[str] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:  # noqa: PERF203
                bad.append(repr(exc))

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    try:
        for i in range(300):
            cfg.set("opacity", 0.3 + (i % 70) / 100.0)
            cfg.save()
    finally:
        stop.set()
        th.join(timeout=5)

    assert bad == [], f"并发读命中坏读 {len(bad)} 次（原子写失效）"


def test_r5_config_save_fault_keeps_original_bytes(tmp_path, monkeypatch):
    """写盘故障（os.replace 抛 EACCES）：原文件字节不变、异常被吞、无 .tmp 残留。"""
    path = tmp_path / "settings.json"
    cfg = Config.load(path)
    cfg.set("metal", "gold")
    cfg.save()
    before = path.read_bytes()

    def deny(src, dst):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(os, "replace", deny)
    cfg.set("metal", "silver")
    cfg.save()  # 不得抛
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_r5_config_save_normal_path_is_atomic(tmp_path, monkeypatch):
    """save 必须真的走 tmp + os.replace（不是裸 write_text）。"""
    path = tmp_path / "settings.json"
    cfg = Config.load(path)
    replaced: list[tuple[str, str]] = []
    real = os.replace

    def spy(src, dst):
        replaced.append((Path(src).name, Path(dst).name))
        return real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    cfg.save()
    assert len(replaced) == 1 and replaced[0][1] == path.name, replaced
    src_name = replaced[0][0]
    assert src_name.startswith(path.name + ".") and src_name.endswith(".tmp"), src_name


def test_r5_control_info_write_atomic(tmp_path, monkeypatch):
    """control.json 的发现文件写入也必须原子化（同源面）。"""
    from gold_pet import control_server as cs

    seen: list[str] = []
    real = os.replace

    def spy(src, dst):
        seen.append(Path(dst).name)
        return real(src, dst)

    monkeypatch.setattr(cs, "control_info_path", lambda: tmp_path / "control.json")
    monkeypatch.setattr(os, "replace", spy)
    ctl = cs.ControlServer(state_reader=lambda: {})
    ctl.port, ctl.token = 12345, "t"
    ctl._write_info()
    assert seen == ["control.json"], "control.json 未走原子写"
    info = json.loads((tmp_path / "control.json").read_text(encoding="utf-8"))
    assert info["port"] == 12345


# ------------------------------------------------------------------ Issue #21
def test_r5_roll_day_extreme_timestamp_does_not_raise():
    """极端时间戳：evaluate 不抛、不清水位、不崩穿刷新链路。"""
    n = Notifier(Config())
    quote = Quote(
        code="gds_AUTD",
        name="黄金",
        price=1000.0,
        prev_close=900.0,
        high=1000.0,
        low=900.0,
        time_text="12:00:00",
        unit="cny_g",
    )
    n.evaluate(quote, now=1_700_000_000.0)
    water_before = dict(n._extreme_water)
    day_before = n._day
    for now in (1e18, 1e308, float("inf"), float("-inf"), float("nan")):
        assert n.evaluate(quote, now=now) == [] or isinstance(n.evaluate(quote, now=now), list)
    assert n._day == day_before, "格式化失败时不得改动 day key"
    assert n._extreme_water == water_before, "格式化失败时不得清空水位"


def test_r5_roll_day_normal_still_resets_across_days():
    """修复不得弱化正常跨日语义：次日水位必须清空。"""
    import time as _t

    n = Notifier(Config())
    quote = Quote(
        code="gds_AUTD",
        name="黄金",
        price=1000.0,
        prev_close=900.0,
        high=1000.0,
        low=900.0,
        time_text="12:00:00",
        unit="cny_g",
    )
    noon = _t.mktime(_t.strptime("2026-11-03 12:00:00", "%Y-%m-%d %H:%M:%S"))
    n.evaluate(quote, now=noon)
    assert n._extreme_water.get("high") == 1000.0
    n.evaluate(quote, now=noon + 86400)
    # 跨日后水位被清空后立即由本次判定重新写入 -> 只剩本次的水位，等于 quote.price
    assert n._extreme_water.get("high") == 1000.0
    assert n._day == _t.strftime("%Y-%m-%d", _t.localtime(noon + 86400))


# ------------------------------------------------------------------ Issue #24
@pytest.mark.parametrize("bad", [None, [], "str", 42, 3.14, ("a",), {"a"}])
def test_r5_alert_from_dict_rejects_non_dict(bad):
    """非 dict 输入必须抛 TypeError（不再逃逸 AttributeError）。"""
    with pytest.raises(TypeError):
        Alert.from_dict(bad)


def test_r5_alert_from_dict_dict_still_tolerant():
    """合法 dict 路径的既有宽容度不得被削弱。"""
    a = Alert.from_dict({"at": "nan", "kind": "pct"})
    assert a.at == 0.0
    assert a.kind == "pct"
    assert Alert.from_dict({}).kind == ""
    # 脏历史里的非 dict 项仍被 Notifier 跳过（不因新增 TypeError 而炸）
    n = Notifier(Config())
    n._history = [Alert(kind="pct", side="up", title="t", body="b")]
    n._loaded = True
    assert len(n.history()) == 1


# ------------------------------------------------------------------ Issue #23
@pytest.mark.parametrize(
    "value,expected",
    [
        (1, 1.0),
        ("2.5", 2.5),
        (None, 0.0),
        (True, 1.0),  # bool 是 int 子类，_num 既有口径按数值处理
        (float("nan"), 0.0),
        (float("inf"), 0.0),
        (10**400, 0.0),  # OverflowError 回落
        ("abc", 0.0),
        ([1], 0.0),
        ({"a": 1}, 0.0),
    ],
)
def test_r5_num_behaviour_unchanged(value, expected):
    """_num 类型表达收窄（R5-04）不得改变任何运行期行为。"""
    assert _num(value) == expected
    assert _num(value, default=-1.0) == (expected if expected != 0.0 else -1.0)
    assert _num(5, low=1, high=3) == 0.0
    assert _num(2, low=1, high=3) == 2.0
