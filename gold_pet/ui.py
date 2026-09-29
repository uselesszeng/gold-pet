"""桌宠窗口、系统托盘与设置面板。"""

from __future__ import annotations

import logging
import random
import time

from PySide6.QtCore import QPoint, QPointF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSlider,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import autostart
from .bubble import (
    ALERT_STYLE,
    BUBBLE_STYLE,
    FLAT_COLOR,
    change_arrow,
    change_color,
    render_cache_html,
    render_line_html,
    render_price_html,
)
from .config import INTERVAL_CHOICES, Config
from .paths import asset_dir
from .price import METALS, Snapshot, is_market_open

# 每种状态的帧数与播放间隔（毫秒）
STATES = {
    "idle": (4, 600),
    "up": (2, 320),
    "down": (2, 320),
    "pat": (2, 300),
    "shy": (3, 260),
    "loading": (2, 400),
    "offline": (1, 1000),
}
# 互动台词
LINES = {
    "up": [
        "金价涨啦，金娘的王冠更亮了✨",
        "涨！今天的金娘心情超好~",
        "要不要趁现在去逛逛金店？",
    ],
    "down": [
        "呜…金娘的金币缩水了",
        "别难过，跌下去的都会回来的",
        "跌了也要笑一笑哦~",
    ],
    "idle": [
        "今天也在认真盯盘呢~",
        "要不要喝杯茶再看看？",
        "金娘会一直陪着你的",
    ],
    "loading": ["正在打听金价…", "稍等一下下~"],
    "offline": ["连不上网了，先看上次的价吧", "网络开小差了…"],
    "welcome": [
        "你好呀，我是金娘～点一下可以摸我的头",
        "右键菜单里能换品种、改设置、让我说话",
        "想让我安静一点就去设置里调刷新间隔哦",
    ],
    "pat": ["摸摸头~金娘很乖的", "呀…被摸头了，好开心", "再摸一下下好不好~"],
    "shy": ["讨厌啦，别一直盯着人家看", "金娘…金娘才没有撒娇呢", "再摸就要闹脾气了哦~"],
    "high": ["创新高啦！要不要看看金条？", "今天涨得好厉害呀~"],
    "low": ["跌到今天最低了…抱抱", "别慌，金娘陪着你"],
}


class PriceWorker(QThread):
    """后台抓行情，避免网络阻塞界面。"""

    # 注意：不要叫 finished，会覆盖 QThread 内置信号
    snapshot_ready = Signal(object)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service

    def run(self):  # noqa: D102 - QThread 入口
        self.snapshot_ready.emit(self.service.fetch())


class SparkLine(QWidget):
    """迷你走势图。"""

    def __init__(self, parent=None, width: int = 96, height: int = 26):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self.values: list[float] = []
        self.color = QColor(FLAT_COLOR)

    def set_series(self, values: list[float], color_hex: str) -> None:
        self.values = list(values)
        self.color = QColor(color_hex)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        if len(self.values) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        lo, hi = min(self.values), max(self.values)
        rng = hi - lo or 1.0
        w, h = self.width(), self.height()
        step = (w - 4) / (len(self.values) - 1)
        pts = [
            QPointF(2 + i * step, h - 3 - (v - lo) / rng * (h - 8))
            for i, v in enumerate(self.values)
        ]
        path = QPainterPath()
        path.moveTo(pts[0])
        for p in pts[1:]:
            path.lineTo(p)
        pen = QPen(self.color)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawPath(path)
        # 半透明填充
        area = QPainterPath(pts[0])
        for p in pts[1:]:
            area.lineTo(p)
        area.lineTo(QPointF(pts[-1].x(), h - 2))
        area.lineTo(QPointF(pts[0].x(), h - 2))
        area.closeSubpath()
        fill = QColor(self.color)
        fill.setAlpha(60)
        painter.fillPath(area, fill)


class PetWindow(QWidget):
    """桌宠主窗口：透明置顶、可拖动、头顶挂价格气泡。"""

    def __init__(self, cfg: Config, service, history=None, auto_refresh: bool = True):
        super().__init__()
        self.cfg = cfg
        self.service = service
        self.history = history
        self.metal = cfg.get("metal", "gold")
        self.unit = cfg.get("unit", "cny_g")
        self.snapshot: Snapshot | None = None
        self.state = "idle"
        self.frame_idx = 0
        self.frames: dict[str, list[QPixmap]] = {}
        self._drag_offset = None
        self._press_at = 0.0
        self._moved = False
        self._last_alert_at = 0.0
        self._last_alert_side = ""
        self._speak_timer = QTimer(self)
        self._speak_timer.setSingleShot(True)
        self._speak_timer.timeout.connect(self._restore_price)
        self._press_timer = QTimer(self)
        self._press_timer.setSingleShot(True)
        self._press_timer.timeout.connect(self._on_long_press)
        self._interact_timer = QTimer(self)
        self._interact_timer.setSingleShot(True)
        self._interact_timer.timeout.connect(self._end_interact)
        self._long_pressed = False
        self._last_extreme_at = 0.0
        self._last_extreme_side = ""
        self._last_target_at = 0.0
        self._last_target_side = ""
        self._fail_streak = 0
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self.refresh)

        self._load_frames()
        self._build_ui()
        self._apply_window_flags()
        self._move_to_default()

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick_frame)
        self.anim_timer.start(STATES[self.state][1])

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh)
        self._apply_interval()

        self.snapshot = self.service.load_cache()
        if self.snapshot and self.snapshot.ok:
            self._render(self.snapshot, silent=True)
        if auto_refresh:
            self.refresh()

    # ------------------------------------------------------------------ 构建
    def _load_frames(self) -> None:
        base = asset_dir() / "pet"
        for state, (count, _delay) in STATES.items():
            pixmaps = []
            for i in range(count):
                path = base / f"{state}_{i}.png"
                if path.exists():
                    pixmaps.append(QPixmap(str(path)))
            if pixmaps:
                self.frames[state] = pixmaps
        if "idle" not in self.frames and self.frames:
            self.frames["idle"] = next(iter(self.frames.values()))

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self.bubble = QLabel("加载中…", self)
        self.bubble.setStyleSheet(BUBBLE_STYLE)
        self.bubble.setAlignment(Qt.AlignCenter)
        self.bubble.setWordWrap(True)
        layout.addWidget(self.bubble, alignment=Qt.AlignCenter)

        self.spark = SparkLine(self)
        layout.addWidget(self.spark, alignment=Qt.AlignCenter)

        self.pet = QLabel(self)
        self.pet.setFixedSize(160, 160)
        self.pet.setAlignment(Qt.AlignCenter)
        self.pet.setPixmap(self._current_pixmap())
        layout.addWidget(self.pet, alignment=Qt.AlignCenter)

        self.setWindowTitle("黄金桌宠")
        icon_path = asset_dir() / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _apply_window_flags(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.cfg.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(float(self.cfg.get("opacity", 1.0)))
        self.bubble.setVisible(bool(self.cfg.get("show_bubble", True)))
        self.spark.setVisible(bool(self.cfg.get("show_spark", True)))

    def _move_to_default(self) -> None:
        """恢复上次位置；若已不在任何屏幕内（换显示器/改分辨率），回落到主屏右下角。"""
        pos = self.cfg.get("pos")
        if isinstance(pos, list) and len(pos) == 2:
            point = QPoint(int(pos[0]), int(pos[1]))
            for screen in QApplication.screens():
                if screen.availableGeometry().contains(point):
                    self.move(point)
                    return
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.right() - self.width() - 24, geo.bottom() - self.height() - 24)

    def _apply_interval(self) -> None:
        self.refresh_timer.start(int(self.cfg.get("interval_sec", 60)) * 1000)

    # ------------------------------------------------------------------ 动画
    def _set_state(self, state: str) -> None:
        if state not in self.frames:
            state = "idle"
        if state == self.state:
            return
        self.state = state
        self.frame_idx = 0
        self.anim_timer.setInterval(STATES[state][1])
        self.pet.setPixmap(self._current_pixmap())

    def _current_pixmap(self) -> QPixmap:
        frames = self.frames.get(self.state) or self.frames.get("idle") or []
        if not frames:
            return QPixmap()
        return frames[self.frame_idx % len(frames)]

    def _tick_frame(self) -> None:
        frames = self.frames.get(self.state) or []
        if len(frames) <= 1:
            return
        self.frame_idx = (self.frame_idx + 1) % len(frames)
        self.pet.setPixmap(self._current_pixmap())

    # ------------------------------------------------------------------ 数据
    def refresh(self) -> None:
        worker = getattr(self, "_worker", None)
        if worker is not None:
            if worker.isRunning():
                return
            # 断开旧 worker，避免旧数据回调
            try:
                worker.snapshot_ready.disconnect(self._on_snapshot)
            except (RuntimeError, TypeError):
                pass
        if self._retry_timer.isActive():
            self._retry_timer.stop()
        self._set_state("loading")
        self._worker = PriceWorker(self.service, self)
        self._worker.snapshot_ready.connect(self._on_snapshot)
        self._worker.start()

    def _on_snapshot(self, snap: Snapshot) -> None:
        if not snap.ok:
            # 失败则指数退避重试：15s → 30s → 60s … 上限 300s
            self._fail_streak += 1
            delay = min(int(self.cfg.get("interval_sec", 60)) * 2 ** (self._fail_streak - 1), 300)
            delay = max(delay, 15)
            logging.getLogger("gold_pet").warning(
                "行情获取失败（第 %d 次）：%s，%ds 后重试",
                self._fail_streak,
                snap.error,
                delay,
            )
            self._retry_timer.start(delay * 1000)
        elif self._fail_streak:
            self._fail_streak = 0
            self._apply_interval()
        self._render(snap)

    def _render(self, snap: Snapshot, silent: bool = False) -> None:
        self.snapshot = snap
        quote = snap.price_of(self.metal, self.unit)
        metal_name = METALS.get(self.metal, {}).get("name", "黄金")

        if quote is None:
            self._set_state("offline")
            cache = self.service.load_cache()
            q = cache.price_of(self.metal, self.unit) if cache else None
            if q is not None:
                self.bubble.setText(render_cache_html(metal_name, q))
            else:
                self.bubble.setText("行情获取失败")
            return

        change = quote.change
        pct = quote.change_pct
        if not snap.ok:
            self._set_state("offline")
        elif change > 0:
            self._set_state("up")
        elif change < 0:
            self._set_state("down")
        else:
            self._set_state("idle")

        # 状态标记：离线 / 休市
        tips: list[str] = []
        if not snap.ok:
            tips.append('<span style="color:#ffd666">离线</span>')
        self.bubble.setText(
            render_price_html(
                metal_name,
                quote,
                updated_at=snap.updated_at,
                tips=tips,
                is_market_open=is_market_open(),
            )
        )
        self._update_spark(quote, change_color(change), append=snap.ok and not silent)

        tray = self.tray_icon()
        if tray is not None:
            tray.setToolTip(
                f"黄金桌宠 {metal_name} {quote.format_price()} {quote.unit} "
                f"{change_arrow(change)}{change:+.2f}"
            )
        if not silent and snap.ok:
            self._maybe_alert(pct)
            self._maybe_extreme(quote)
            self._maybe_target(quote)

    def _update_spark(self, quote, color: str, append: bool = False) -> None:
        """更新迷你走势图；append=True 才落盘（只在联网刷新成功时写，减少磁盘写入）。"""
        if self.history is None or not self.cfg.get("show_spark", True):
            self.spark.setVisible(False)
            return
        self.spark.setVisible(True)
        if append:
            series = self.history.append(self.metal, self.unit, quote.price)
        else:
            series = self.history.series(self.metal, self.unit)
        values = [p.get("p", 0.0) for p in series][-60:]
        self.spark.set_series(values, color)

    def _maybe_target(self, quote) -> None:
        """到价提醒：以元/克为基准（与当前显示单位无关）。"""
        high = float(self.cfg.get("target_high", 0.0))
        low = float(self.cfg.get("target_low", 0.0))
        if high <= 0 and low <= 0:
            return
        base = quote if quote.unit.startswith("元") else None
        if base is None and self.snapshot is not None:
            base = self.snapshot.price_of(self.metal, "cny_g")
        if base is None:
            return
        now = time.time()
        cooldown = self._cooldown_sec()
        if high > 0 and base.price >= high:
            side, text = "high", f"金价已涨到 {base.price:.2f} 元/克（目标 {high:.2f}）"
        elif low > 0 and base.price <= low:
            side, text = "low", f"金价已跌到 {base.price:.2f} 元/克（目标 {low:.2f}）"
        else:
            return
        if now - self._last_target_at < cooldown and side == self._last_target_side:
            return
        self._last_target_at = now
        self._last_target_side = side
        self.speak("high" if side == "high" else "low")
        tray = self.tray_icon()
        if tray is not None:
            tray.showMessage("黄金桌宠 · 到价提醒", text, QSystemTrayIcon.Information, 8000)

    def _cooldown_sec(self) -> int:
        return int(self.cfg.get("alert_cooldown_sec", 600))

    def _maybe_alert(self, pct: float) -> None:
        threshold = float(self.cfg.get("alert_pct", 0.5))
        if threshold <= 0 or abs(pct) < threshold:
            return
        now = time.time()
        side = "up" if pct > 0 else "down"
        cooldown = self._cooldown_sec()
        if now - self._last_alert_at < cooldown and side == self._last_alert_side:
            return
        self._last_alert_at = now
        self._last_alert_side = side
        self.bubble.setStyleSheet(ALERT_STYLE)
        QTimer.singleShot(8000, lambda: self.bubble.setStyleSheet(BUBBLE_STYLE))
        # 触发提醒时顺便让金娘说句话（同一提醒 10 分钟内只说一次）
        self.speak(side)
        tray = self.tray_icon()
        if tray is not None:
            metal_name = METALS.get(self.metal, {}).get("name", "黄金")
            tray.showMessage(
                "黄金桌宠提醒",
                f"{metal_name}价{'上涨' if pct > 0 else '下跌'} {abs(pct):.2f}%，"
                f"已超过设定阈值 {threshold:.2f}%",
                QSystemTrayIcon.Information,
                8000,
            )

    def _maybe_extreme(self, quote) -> None:
        """创新高 / 创新低提醒（同一方向 10 分钟内只提醒一次）。"""
        if quote.high <= 0 and quote.low <= 0:
            return
        if quote.high > 0 and quote.price >= quote.high:
            side, title = "high", "创新高"
        elif quote.low > 0 and quote.price <= quote.low:
            side, title = "low", "创新低"
        else:
            return
        now = time.time()
        cooldown = self._cooldown_sec()
        if now - self._last_extreme_at < cooldown and side == self._last_extreme_side:
            return
        self._last_extreme_at = now
        self._last_extreme_side = side
        metal_name = METALS.get(self.metal, {}).get("name", "黄金")
        self.speak(side)
        tray = self.tray_icon()
        if tray is not None:
            tray.showMessage(
                "黄金桌宠提醒",
                f"{metal_name}{title}：{quote.format_price()} {quote.unit}"
                f"（今日高 {quote.high:.2f} / 低 {quote.low:.2f}）",
                QSystemTrayIcon.Information,
                8000,
            )

    # ------------------------------------------------------------------ 台词
    def speak(self, mood: str | None = None) -> None:
        """气泡里说一句话，4 秒后恢复价格显示。"""
        mood = mood or self.state if self.state in LINES else "idle"
        if mood not in LINES:
            mood = "idle"
        self.bubble.setText(render_line_html(random.choice(LINES[mood])))
        self._speak_timer.start(4000)

    def _restore_price(self) -> None:
        if self.snapshot is not None:
            self._render(self.snapshot, silent=True)

    # ------------------------------------------------------------------ 交互
    def tray_icon(self):
        return getattr(self, "_tray", None)

    def attach_tray(self, tray: QSystemTrayIcon) -> None:
        self._tray = tray

    # ------------------------------------------------------- 摸头 / 撒娇
    def pat(self) -> None:
        """摸头：眯眼笑 + 台词，1.5 秒后回到行情状态。"""
        self.speak("pat")
        self._play_state("pat", 1500)

    def shy(self) -> None:
        """撒娇：眼神飘开 + 台词，2.5 秒后回到行情状态。"""
        self.speak("shy")
        self._play_state("shy", 2500)

    def _play_state(self, state: str, ms: int) -> None:
        self._set_state(state)
        self._interact_timer.start(ms)

    def _on_long_press(self) -> None:
        if self._drag_offset is not None and not self._moved:
            self._long_pressed = True
            self.shy()

    def _end_interact(self) -> None:
        if self.snapshot is not None:
            self._render(self.snapshot, silent=True)
        else:
            self._set_state("idle")

    def toggle_unit(self) -> None:
        self.unit = "usd_oz" if self.unit == "cny_g" else "cny_g"
        self.cfg.set("unit", self.unit)
        self.cfg.save()
        if self.snapshot:
            self._render(self.snapshot, silent=True)

    def toggle_metal(self) -> None:
        order = list(METALS.keys())
        idx = order.index(self.metal) if self.metal in order else 0
        self.metal = order[(idx + 1) % len(order)]
        self.cfg.set("metal", self.metal)
        self.cfg.save()
        self.speak("idle")
        self.refresh()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_at = time.time()
            self._moved = False
            self._long_pressed = False
            self._press_timer.start(600)  # 长按 600ms 触发撒娇
            event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._moved = True
            event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._drag_offset is None:
            return
        self._press_timer.stop()
        was_long = self._long_pressed
        self._drag_offset = None
        quick = (time.time() - self._press_at) < 0.25
        if self._moved:
            self.cfg.set("pos", [self.x(), self.y()])
            self.cfg.save()
        elif was_long:
            pass  # 撒娇已由长按触发
        elif quick:
            self.pat()  # 点一下 = 摸摸头
        event.accept()

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        self.refresh()
        event.accept()

    def contextMenuEvent(self, event):  # noqa: N802
        menu = QMenu(self)
        menu.addAction("立即刷新", self.refresh)
        menu.addAction("切换单位（元/克 ↔ 美元/盎司）", self.toggle_unit)
        menu.addAction(f"切换品种（当前：{METALS[self.metal]['name']}）", self.toggle_metal)
        menu.addSeparator()
        menu.addAction("摸摸头", self.pat)
        menu.addAction("撒娇一下", self.shy)
        menu.addAction("让金娘说句话", lambda: self.speak())
        menu.addSeparator()

        act_top = menu.addAction("窗口置顶")
        act_top.setCheckable(True)
        act_top.setChecked(bool(self.cfg.get("always_on_top", True)))
        act_top.triggered.connect(self._toggle_top)

        act_bubble = menu.addAction("显示价格气泡")
        act_bubble.setCheckable(True)
        act_bubble.setChecked(bool(self.cfg.get("show_bubble", True)))
        act_bubble.triggered.connect(self._toggle_bubble)

        act_spark = menu.addAction("显示迷你走势图")
        act_spark.setCheckable(True)
        act_spark.setChecked(bool(self.cfg.get("show_spark", True)))
        act_spark.triggered.connect(self._toggle_spark)

        menu.addSeparator()
        menu.addSeparator()
        menu.addAction("设置…", self.open_settings)
        if self.tray_icon() is not None:
            menu.addAction("隐藏到托盘", self.hide)
        menu.addAction("退出", self.quit_app)
        menu.exec(event.globalPos())

    def _toggle_top(self, checked: bool) -> None:
        self.cfg.set("always_on_top", checked)
        self.cfg.save()
        self._apply_window_flags()
        self.show()

    def _toggle_bubble(self, checked: bool) -> None:
        self.cfg.set("show_bubble", checked)
        self.cfg.save()
        self.bubble.setVisible(checked)

    def _toggle_spark(self, checked: bool) -> None:
        self.cfg.set("show_spark", checked)
        self.cfg.save()
        self.spark.setVisible(checked)
        if checked and self.snapshot is not None:
            self._render(self.snapshot, silent=True)

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            self.metal = self.cfg.get("metal", "gold")
            self.unit = self.cfg.get("unit", "cny_g")
            self._apply_window_flags()
            self._apply_interval()
            self.bubble.setVisible(bool(self.cfg.get("show_bubble", True)))
            self.show()
            self.refresh()

    def quit_app(self) -> None:
        self.cfg.set("pos", [self.x(), self.y()])
        self.cfg.save()
        self.anim_timer.stop()
        self.refresh_timer.stop()
        worker = getattr(self, "_worker", None)
        if worker is not None and worker.isRunning():
            worker.quit()
            worker.wait(10000)
        QApplication.quit()

    def closeEvent(self, event):  # noqa: N802
        # 有托盘：关窗=藏起来；没托盘：关窗=退出，避免再也唤不回来
        if self.tray_icon() is not None:
            event.ignore()
            self.hide()
        else:
            self.cfg.set("pos", [self.x(), self.y()])
            self.cfg.save()
            event.accept()
            QApplication.quit()


class SettingsDialog(QDialog):
    """设置面板。"""

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("黄金桌宠 · 设置")
        self.setModal(True)

        self.metal_combo = QComboBox()
        for mid, meta in METALS.items():
            self.metal_combo.addItem(meta["name"], mid)
        cur_metal = cfg.get("metal", "gold")
        idx = self.metal_combo.findData(cur_metal)
        self.metal_combo.setCurrentIndex(max(0, idx))

        self.unit_combo = QComboBox()
        self.unit_combo.addItem("元/克（国内现货）", "cny_g")
        self.unit_combo.addItem("美元/盎司（国际）", "usd_oz")
        self.unit_combo.setCurrentIndex(0 if cfg.get("unit") == "cny_g" else 1)

        self.interval_combo = QComboBox()
        for sec in INTERVAL_CHOICES:
            self.interval_combo.addItem(f"{sec} 秒", sec)
        cur = int(cfg.get("interval_sec", 60))
        self.interval_combo.setCurrentIndex(
            INTERVAL_CHOICES.index(cur) if cur in INTERVAL_CHOICES else 0
        )

        self.alert_spin = QDoubleSpinBox()
        self.alert_spin.setRange(0.0, 20.0)
        self.alert_spin.setSingleStep(0.1)
        self.alert_spin.setSuffix(" %")
        self.alert_spin.setValue(float(cfg.get("alert_pct", 0.5)))

        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(1, 120)
        self.cooldown_spin.setSingleStep(1)
        self.cooldown_spin.setSuffix(" 分钟")
        self.cooldown_spin.setValue(int(cfg.get("alert_cooldown_sec", 600)) // 60)

        # 到价提醒（以元/克为基准，0 = 关闭）
        self.target_high_spin = QDoubleSpinBox()
        self.target_high_spin.setRange(0.0, 100000.0)
        self.target_high_spin.setDecimals(2)
        self.target_high_spin.setSuffix(" 元/克")
        self.target_high_spin.setValue(float(cfg.get("target_high", 0.0)))

        self.target_low_spin = QDoubleSpinBox()
        self.target_low_spin.setRange(0.0, 100000.0)
        self.target_low_spin.setDecimals(2)
        self.target_low_spin.setSuffix(" 元/克")
        self.target_low_spin.setValue(float(cfg.get("target_low", 0.0)))

        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(int(float(cfg.get("opacity", 1.0)) * 100))

        self.top_check = QCheckBox("窗口置顶")
        self.top_check.setChecked(bool(cfg.get("always_on_top", True)))

        self.bubble_check = QCheckBox("显示价格气泡")
        self.bubble_check.setChecked(bool(cfg.get("show_bubble", True)))

        self.spark_check = QCheckBox("显示迷你走势图")
        self.spark_check.setChecked(bool(cfg.get("show_spark", True)))

        self.autostart_check = QCheckBox("开机自动启动")
        self.autostart_check.setChecked(autostart.is_enabled())

        form = QFormLayout()
        form.addRow("关注品种", self.metal_combo)
        form.addRow("默认单位", self.unit_combo)
        form.addRow("刷新间隔", self.interval_combo)
        form.addRow("涨跌提醒阈值", self.alert_spin)
        form.addRow("同类提醒冷却", self.cooldown_spin)
        form.addRow("到价提醒 ≥（0 关闭）", self.target_high_spin)
        form.addRow("到价提醒 ≤（0 关闭）", self.target_low_spin)
        form.addRow("不透明度", self.opacity_slider)
        form.addRow("", self.top_check)
        form.addRow("", self.bubble_check)
        form.addRow("", self.spark_check)
        form.addRow("", self.autostart_check)

        ok = QPushButton("保存")
        cancel = QPushButton("取消")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)

    def accept(self):  # noqa: D102
        self.cfg.set("metal", self.metal_combo.currentData())
        self.cfg.set("unit", self.unit_combo.currentData())
        self.cfg.set("interval_sec", int(self.interval_combo.currentData()))
        self.cfg.set("alert_pct", float(self.alert_spin.value()))
        self.cfg.set("alert_cooldown_sec", int(self.cooldown_spin.value()) * 60)
        self.cfg.set("target_high", float(self.target_high_spin.value()))
        self.cfg.set("target_low", float(self.target_low_spin.value()))
        self.cfg.set("opacity", self.opacity_slider.value() / 100.0)
        self.cfg.set("always_on_top", self.top_check.isChecked())
        self.cfg.set("show_bubble", self.bubble_check.isChecked())
        self.cfg.set("show_spark", self.spark_check.isChecked())
        self.cfg.set("autostart", self.autostart_check.isChecked())
        autostart.set_enabled(self.autostart_check.isChecked())
        self.cfg.save()
        super().accept()


def build_menu(window: PetWindow) -> QMenu:
    """统一的菜单构建：右键菜单与托盘菜单共用。"""
    menu = QMenu(window)
    menu.addAction("立即刷新", window.refresh)
    menu.addAction("切换单位", window.toggle_unit)
    menu.addAction("切换品种（当前：" + METALS[window.metal]["name"] + "）", window.toggle_metal)
    menu.addSeparator()
    menu.addAction("摸摸头", window.pat)
    menu.addAction("撒娇一下", window.shy)
    menu.addAction("让金娘说句话", lambda: window.speak())
    menu.addSeparator()

    act_top = menu.addAction("窗口置顶")
    act_top.setCheckable(True)
    act_top.setChecked(bool(window.cfg.get("always_on_top", True)))
    act_top.triggered.connect(window._toggle_top)

    act_bubble = menu.addAction("显示价格气泡")
    act_bubble.setCheckable(True)
    act_bubble.setChecked(bool(window.cfg.get("show_bubble", True)))
    act_bubble.triggered.connect(window._toggle_bubble)

    act_spark = menu.addAction("显示迷你走势图")
    act_spark.setCheckable(True)
    act_spark.setChecked(bool(window.cfg.get("show_spark", True)))
    act_spark.triggered.connect(window._toggle_spark)

    menu.addSeparator()
    menu.addAction("设置…", window.open_settings)
    menu.addAction("关于…", window.open_about)
    if window.tray_icon() is not None:
        menu.addAction("隐藏到托盘", window.hide)
    menu.addAction("退出", window.quit_app)
    return menu


def build_tray(window: PetWindow, cfg: Config) -> QSystemTrayIcon | None:
    """创建系统托盘图标与菜单；托盘不可用时返回 None（调用方需降级）。"""
    if not QSystemTrayIcon.isSystemTrayAvailable():
        logging.getLogger("gold_pet").warning("系统托盘不可用，已降级为关窗即退出")
        return None
    icon_path = asset_dir() / "icon.png"
    icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
    tray = QSystemTrayIcon(icon, window)
    tray.setToolTip("黄金桌宠")

    menu = build_menu(window)
    show_act = menu.addAction("显示/隐藏")
    show_act.triggered.connect(lambda: window.setVisible(not window.isVisible()))
    menu.removeAction(show_act)
    menu.insertAction(menu.actions()[0], show_act)
    menu.insertSeparator(menu.actions()[1])

    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: (
            window.setVisible(not window.isVisible()) if reason == QSystemTrayIcon.Trigger else None
        )
    )
    window.attach_tray(tray)
    tray.show()
    return tray
