"""气泡渲染单测（纯函数，不需要 Qt）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gold_pet.bubble import (  # noqa: E402
    DOWN_COLOR,
    FLAT_COLOR,
    UP_COLOR,
    change_arrow,
    change_color,
    render_cache_html,
    render_line_html,
    render_price_html,
)
from gold_pet.price import Quote  # noqa: E402


def _quote(price: float, prev: float, high: float = 0.0, low: float = 0.0) -> Quote:
    return Quote(
        code="test",
        name="黄金",
        price=price,
        prev_close=prev,
        high=high,
        low=low,
        unit="元/克",
        currency="CNY",
    )


def test_colors_and_arrows():
    assert change_color(1.0) == UP_COLOR
    assert change_color(-1.0) == DOWN_COLOR
    assert change_color(0.0) == FLAT_COLOR
    assert change_arrow(1.0) == "▲"
    assert change_arrow(-1.0) == "▼"
    assert change_arrow(0.0) == "—"


def test_price_html_contains_all_parts():
    html = render_price_html(
        "黄金", _quote(897.15, 900.37, 902.0, 889.24), updated_at=0, is_market_open=True
    )
    assert "897.15" in html
    assert "元/克" in html
    assert "-3.22" in html
    assert "-0.36%" in html
    assert "高 902.00" in html and "低 889.24" in html
    assert "更新" in html
    assert "休市" not in html


def test_market_closed_shows_tag():
    html = render_price_html("黄金", _quote(897.15, 900.37), is_market_open=False)
    assert "休市" in html


def test_tips_are_appended():
    html = render_price_html(
        "黄金",
        _quote(897.15, 900.37),
        tips=['<span style="color:#ffd666">离线</span>'],
        is_market_open=True,
    )
    assert "离线" in html


def test_line_and_cache_html():
    assert "你好" in render_line_html("你好")
    html = render_cache_html("黄金", _quote(897.15, 900.37))
    assert "897.15" in html and "离线" in html


def test_line_html_escapes_external_text():
    """P2-01：外部文本（如 /say 自由发言）必须转义，不能注入 HTML/本地图片。"""
    out = render_line_html('<img src="file:///C:/x.png" width=800>')
    assert "<img" not in out
    assert "&lt;img" in out
    # 普通文本不受转义影响
    assert "主人好呀" in render_line_html("主人好呀")
