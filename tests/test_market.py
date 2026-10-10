"""交易时段判断（is_market_open）的边界测试。

重点覆盖夜盘跨零点的归属规则：
凌晨 00:00-02:30 属于「前一个交易日」的夜盘，因此
  - 周一凌晨 = 周日，无夜盘 → 休市
  - 周六凌晨 = 周五夜盘 → 开市
这两条是原实现遗漏的场景（周六被误判为开市、周一凌晨被误判为开市）。

注意：构造 struct_time 必须位置传全 9 项，否则 tm_wday 默认为 0（周一），
会让周末判断失效——早期用例正是踩了这个坑。
"""

from __future__ import annotations

import calendar
import datetime
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gold_pet.price import is_market_open  # noqa: E402


def stamp(y: int, m: int, d: int, h: int, mi: int) -> time.struct_time:
    """构造带正确 tm_wday / tm_yday 的 struct_time。"""
    wday = calendar.weekday(y, m, d)
    yday = datetime.date(y, m, d).timetuple().tm_yday
    return time.struct_time((y, m, d, h, mi, 0, wday, yday, 0))


@pytest.mark.parametrize(
    ("y", "m", "d", "h", "mi", "expected", "desc"),
    [
        # 日盘
        (2026, 9, 30, 9, 0, True, "周三 09:00 日盘开盘"),
        (2026, 9, 30, 10, 0, True, "周三 10:00 日盘"),
        (2026, 9, 30, 11, 30, True, "周三 11:30 日盘收盘边界"),
        (2026, 9, 30, 11, 31, False, "周三 11:31 午休"),
        (2026, 9, 30, 12, 0, False, "周三 12:00 午休"),
        (2026, 9, 30, 13, 30, True, "周三 13:30 午后开盘"),
        (2026, 9, 30, 14, 0, True, "周三 14:00 午后"),
        (2026, 9, 30, 15, 30, True, "周三 15:30 日盘收盘边界"),
        (2026, 9, 30, 16, 0, False, "周三 16:00 盘间休息"),
        (2026, 9, 30, 19, 59, False, "周三 19:59 夜盘前"),
        (2026, 9, 30, 20, 0, True, "周三 20:00 夜盘开盘边界"),
        (2026, 9, 30, 21, 0, True, "周三 21:00 夜盘"),
        (2026, 9, 30, 23, 59, True, "周三 23:59 夜盘"),
        # 夜盘跨零点
        (2026, 9, 30, 0, 30, True, "周三凌晨 00:30 = 周二夜盘延续"),
        (2026, 10, 1, 1, 0, True, "周四凌晨 01:00 = 周三夜盘延续"),
        (2026, 10, 1, 2, 30, True, "周四 02:30 夜盘收盘边界"),
        (2026, 10, 1, 2, 31, False, "周四 02:31 夜盘已收"),
        (2026, 10, 1, 3, 0, False, "周四 03:00 夜盘已收"),
        # 周末
        (2026, 10, 3, 10, 0, False, "周六 10:00 休市"),
        (2026, 10, 3, 21, 0, False, "周六 21:00 休市（无周六夜盘）"),
        (2026, 10, 4, 10, 0, False, "周日 10:00 休市"),
        (2026, 10, 4, 22, 0, False, "周日 22:00 休市"),
        # 周末与夜盘的交叉归属（原实现的错误点）
        (2026, 10, 3, 1, 0, True, "周六凌晨 01:00 = 周五夜盘延续"),
        (2026, 9, 28, 1, 0, False, "周一凌晨 01:00 = 周日无夜盘 → 休市"),
        (2026, 9, 28, 0, 30, False, "周一凌晨 00:30 → 休市"),
        (2026, 9, 28, 10, 0, True, "周一 10:00 日盘开盘"),
        (2026, 10, 2, 2, 0, True, "周五凌晨 02:00 = 周四夜盘延续"),
    ],
)
def test_market_open_boundaries(y, m, d, h, mi, expected, desc):
    assert is_market_open(stamp(y, m, d, h, mi)) is expected, desc


def test_market_open_defaults_to_now():
    """不传参数时应使用当前本地时间，返回 bool 且不抛异常。"""
    assert isinstance(is_market_open(), bool)


def test_weekend_morning_is_not_open():
    """回归：原实现对周六上午 10 点返回了 True（夜盘分支绕过了周末判断）。"""
    assert is_market_open(stamp(2026, 10, 3, 10, 0)) is False


def test_monday_dawn_is_not_open():
    """回归：周一凌晨属于周日，无夜盘，应为休市。"""
    assert is_market_open(stamp(2026, 9, 28, 0, 30)) is False
