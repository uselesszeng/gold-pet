import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time  # noqa: E402

from gold_pet.price import is_market_open  # noqa: E402


def _at(hour: int, minute: int, wday: int = 1) -> time.struct_time:
    # wday: 0=周一 ... 4=周五, 5/6=周末
    return time.struct_time((2026, 9, 28 + wday, hour, minute, 0, wday, 261, 0))


def test_market_open_sessions():
    assert is_market_open(_at(10, 0))  # 日盘
    assert is_market_open(_at(14, 0))  # 日盘下午
    assert is_market_open(_at(21, 0))  # 夜盘
    assert is_market_open(_at(1, 0))  # 夜盘次日凌晨


def test_market_closed():
    assert not is_market_open(_at(12, 0))  # 午休
    assert not is_market_open(_at(16, 0))  # 收盘后
    assert not is_market_open(_at(8, 0))  # 开盘前
    assert not is_market_open(_at(10, 0, wday=5))  # 周六
    assert not is_market_open(_at(10, 0, wday=6))  # 周日
