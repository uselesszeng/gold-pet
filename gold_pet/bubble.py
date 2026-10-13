"""气泡渲染：把「行情 → 气泡 HTML」的展示逻辑从窗口代码里剥离出来。

纯函数、无 Qt 依赖，方便单测与后续换样式。
"""

from __future__ import annotations

import html
import time

from .price import Quote, safe_strftime

# 涨红跌绿（国内习惯）
UP_COLOR = "#ff4d4f"
DOWN_COLOR = "#16c784"
FLAT_COLOR = "#8c8c8c"

BUBBLE_STYLE = (
    "background-color: rgba(28, 28, 32, 210);"
    "color: #f5f5f5;"
    "border: 1px solid rgba(255, 215, 120, 160);"
    "border-radius: 10px;"
    "padding: 6px 10px;"
    "font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif;"
)

ALERT_STYLE = (
    "background-color: rgba(120, 30, 30, 225);"
    "color: #fff;"
    "border: 1px solid #ffd978;"
    "border-radius: 10px;"
    "padding: 6px 10px;"
    "font-family: 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif;"
)


def change_color(change: float) -> str:
    return UP_COLOR if change > 0 else DOWN_COLOR if change < 0 else FLAT_COLOR


def change_arrow(change: float) -> str:
    return "▲" if change > 0 else "▼" if change < 0 else "—"


def render_price_html(
    metal_name: str,
    quote: Quote,
    updated_at: float | None = None,
    tips: list[str] | None = None,
    is_market_open: bool = True,
) -> str:
    """渲染价格气泡内容。

    tips 为额外状态标记（离线 / 休市 等），由调用方决定是否传入。
    """
    color = change_color(quote.change)
    arrow = change_arrow(quote.change)
    sign = "+" if quote.change > 0 else ""
    tips = list(tips or [])
    if not is_market_open:
        tips.append('<span style="color:#9aa0a6">休市</span>')
    tip_html = " · ".join(tips)

    range_html = ""
    if quote.high > 0 and quote.low > 0:
        range_html = (
            f'<div style="font-size:11px;color:#bbbbbb">'
            f"高 {quote.high:.2f} · 低 {quote.low:.2f}</div>"
        )
    footer = ""
    if updated_at is not None:
        # R5-BUG-4：updated_at 可能来自手改缓存（1e308 会让 localtime 抛
        # OverflowError 崩掉整个刷新渲染链），统一走安全格式化。
        clock = safe_strftime("%H:%M:%S", updated_at)
        suffix = f" · {tip_html}" if tip_html else ""
        footer = f'<div style="font-size:11px;color:#aaaaaa">更新 {clock}{suffix}</div>'
    elif tip_html:
        footer = f'<div style="font-size:11px;color:#aaaaaa">{tip_html}</div>'
    return (
        f'<div style="font-size:13px">{metal_name} {quote.format_price()} {quote.unit}</div>'
        f'<div style="font-size:12px;color:{color}">'
        f"{arrow} {sign}{quote.change:.2f} ({sign}{quote.change_pct:.2f}%)</div>"
        f"{range_html}{footer}"
    )


def render_line_html(text: str) -> str:
    """渲染一句话气泡（台词 / 引导）。

    P2-01：text 统一做 HTML 转义后再插值。QLabel 按富文本渲染，未转义的
    外部文本（如 AI 控制服务 /say 的自由发言）可注入 <img>/<table> 等标签，
    甚至加载本地图片。转义收口在渲染边界，台词池等内部文本不含 HTML 标记，
    显示不受影响；/say 端点不再自行转义，避免双重转义。
    """
    return f'<div style="font-size:12px;color:#ffd978">{html.escape(text)}</div>'


def render_cache_html(metal_name: str, quote: Quote, updated_at: float | None = None) -> str:
    """离线时展示上次缓存的价格。

    updated_at 为缓存写入时间，会显示为「缓存于 xx:xx」并在超过 1 小时时提示数据陈旧，
    避免用户把几天前的价格当成现价。
    """
    stamp = ""
    if updated_at:
        # R5-BUG-4：同上，缓存时间戳不可信
        clock = safe_strftime("%m-%d %H:%M", updated_at)
        age = time.time() - updated_at
        if age >= 3600:
            hours = int(age // 3600)
            stale = f" · 已过期 {hours} 小时" if hours < 48 else " · 数据陈旧"
            stamp = f'<div style="font-size:11px;color:#ffd666">缓存于 {clock}{stale}</div>'
        else:
            stamp = f'<div style="font-size:11px;color:#aaaaaa">缓存于 {clock}</div>'
    return (
        f"{metal_name} {quote.format_price()} {quote.unit}<br>"
        f'<span style="color:{FLAT_COLOR}">离线 · 缓存</span>{stamp}'
    )
