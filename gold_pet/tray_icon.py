"""托盘图标渲染：把当前价格画成托盘数字图标。

Windows 托盘图标只有 16×16，最多容纳 3-4 个字符，因此只绘制价格的整数部分
（如 901.75 → "901"），并保留涨跌配色；完整精度仍通过 tooltip 展示。

本模块只依赖 QtGui（不依赖窗口），便于单测。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from .bubble import DOWN_COLOR, FLAT_COLOR, UP_COLOR

ICON_SIZE = 16
# 整数部分超过该位数就画不下，退回纯色圆点+箭头
MAX_DIGITS = 4

_BG_UP = QColor("#3a1214")
_BG_DOWN = QColor("#0f2b21")
_BG_FLAT = QColor("#2a2a2e")


def tray_text(price: float, max_digits: int = MAX_DIGITS) -> str:
    """把价格转成托盘上要显示的短文本（只取整数部分）。

    整数部分位数超过 max_digits 时返回空串，调用方应退回默认图标。
    """
    try:
        value = float(price)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    whole = int(abs(value))
    text = str(whole)
    if len(text) > max_digits:
        return ""
    return text


def _bg_color(change: float) -> QColor:
    if change > 0:
        return _BG_UP
    if change < 0:
        return _BG_DOWN
    return _BG_FLAT


def _fg_color(change: float) -> QColor:
    if change > 0:
        return QColor(UP_COLOR)
    if change < 0:
        return QColor(DOWN_COLOR)
    return QColor(FLAT_COLOR)


def render_price_icon(price: float, change: float = 0.0, size: int = ICON_SIZE) -> QIcon | None:
    """渲染价格数字图标；价格无法显示为数字时返回 None。

    数字位数自适应：1-2 位用大号字，3 位用中号，4 位用小号。
    """
    text = tray_text(price)
    if not text:
        return None

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        # 圆角底板，让数字在浅色/深色任务栏上都可辨
        painter.setPen(Qt.NoPen)
        painter.setBrush(_bg_color(change))
        painter.drawRoundedRect(QRect(0, 0, size, size), 3, 3)

        # 字号自适应：位数为 1-2 时 11pt，3 位 10pt，4 位 8pt
        points = {1: 11, 2: 11, 3: 10, 4: 8}.get(len(text), 8)
        font = QFont("Arial", points)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(_fg_color(change))
        painter.drawText(QRect(0, 0, size, size), Qt.AlignCenter, text)
    finally:
        painter.end()

    return QIcon(pixmap)


def render_fallback_icon(base: QIcon | None = None, size: int = ICON_SIZE) -> QIcon | None:
    """拿不到可显示的价格时用默认图标（assets/icon.png 或纯色点）。"""
    if base is not None and not base.isNull():
        return base
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#ffd34d"))
        painter.drawEllipse(2, 2, size - 4, size - 4)
    finally:
        painter.end()
    return QIcon(pixmap)
