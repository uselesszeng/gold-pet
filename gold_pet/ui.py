"""桌宠窗口、系统托盘与设置面板。"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, Qt, QThread, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSystemTrayIcon,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from . import __version__, autostart, shortcut
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
from .care import CARE_KINDS, CareScheduler
from .config import (
    ACTIVITY_LEVELS,
    CARE_INTERVAL_CHOICES,
    INTERVAL_CHOICES,
    SPARK_RANGE_NAMES,
    Config,
)
from .control_server import ControlServer
from .notifier import (
    RULE_CONFIG_KEYS,
    Alert,
    Notifier,
    _safe_float,
    filter_series,
    in_quiet_hours,
)
from .paths import alert_history_path, asset_dir
from .price import METALS, Snapshot, is_market_open

# 每种状态的帧数与播放间隔（毫秒）
# v0.6.0：素材替换为 dsh-whale-pet 鲸鱼娘（帧数/间隔按各 GIF 实测平均延迟）
# 素材来源与许可见 README「素材致谢与许可」
STATES = {
    "idle": (6, 180),
    "up": (5, 170),
    "down": (8, 150),
    "pat": (4, 175),
    "shy": (6, 170),
    "loading": (6, 140),
    "offline": (6, 170),
}
# 互动台词（v0.7 扩充：每组加量减少重复感，并新增关怀提醒三组）
LINES = {
    "up": [
        "金价涨啦，鲸娘的尾巴翘起来啦✨",
        "涨！今天的鲸娘心情超好~",
        "要不要趁现在去逛逛金店？",
        "红彤彤的，看着就喜庆~",
        "涨得这么精神，主人也要加油哦",
        "鲸娘的小金库又鼓了一点点",
        "这波涨势，稳中带甜~",
        "涨啦涨啦！主人快看快看！",
    ],
    "down": [
        "呜…鲸娘的金币缩水了",
        "别难过，跌下去的都会回来的",
        "跌了也要笑一笑哦~",
        "黄金嘛，跌一跌才好上车呀",
        "鲸娘帮你盯着呢，别担心",
        "风物长宜放眼量，主人~",
        "跌的时候最适合喝口热茶",
        "金子还在，咱们不慌",
    ],
    "idle": [
        "今天也在认真盯盘呢~",
        "要不要喝杯茶再看看？",
        "鲸娘会一直陪着你的",
        "行情平平淡淡，主人平平安安",
        "鲸娘站在这儿，好像有点困…",
        "啧，金价怎么还不动呀",
        "主人在忙什么呀？",
        "（鲸娘在数自己的鳞片玩）",
        "盯盘中…眼睛都不敢眨",
        "有点想鲸鱼妈妈了…",
    ],
    "loading": [
        "正在打听金价…",
        "稍等一下下~",
        "鲸娘去问问行情~",
        "马上就好，主人别急",
        "翻行情小本本中…",
    ],
    "offline": [
        "连不上网了，先看上次的价吧",
        "网络开小差了…",
        "断网中，鲸娘也只能干等",
        "网线是不是被猫咬了呀？",
        "等网络回来，鲸娘马上更新~",
    ],
    "welcome": [
        "你好呀，我是鲸娘～点一下可以摸我的头",
        "右键菜单里能换品种、改设置、让我说话",
        "想让我安静一点就去设置里调刷新间隔哦",
        "鲸娘入住主人的桌面啦，请多关照~",
        "我会一直帮你盯着金价的！",
    ],
    "pat": [
        "摸摸头~鲸娘很乖的",
        "呀…被摸头了，好开心",
        "再摸一下下好不好~",
        "主人的手好暖和呀",
        "摸头充电中…电量 +1",
        "鲸娘的头发不会掉色的哦",
        "嘿嘿，最喜欢主人了",
        "被摸头的鲸娘干劲满满！",
    ],
    "shy": [
        "讨厌啦，别一直盯着人家看",
        "鲸娘…鲸娘才没有撒娇呢",
        "再摸就要闹脾气了哦~",
        "呜，脸都红了啦",
        "人家会不好意思的嘛…",
        "盯、盯太久啦……",
        "主人坏坏，欺负鲸娘",
        "（小声）其实再摸一下也可以…",
    ],
    "high": [
        "创新高啦！要不要看看金条？",
        "今天涨得好厉害呀~",
        "新高！鲸娘要转圈圈庆祝！",
        "哇，今天的金价闪闪发光✨",
    ],
    "low": [
        "跌到今天最低了…抱抱",
        "别慌，鲸娘陪着你",
        "今天最低啦，金价也累了吧",
        "抱住主人的大腿不撒手…",
    ],
    # 关怀提醒（v0.7）：只走气泡，遵守勿扰时段
    "care_drink": [
        "主人，该喝水啦💧鲸娘盯着呢",
        "喝水时间到！小口小口喝~",
        "鲸娘的鱼缸都该换水了，主人也喝口水吧💧",
        "多喝水，皮肤才会像鲸娘一样好~",
        "杯子空了吧？去接点水回来继续忙~",
        "补水提醒！喝完记得夸夸鲸娘",
        "主人主人，喝口水休息一下眼睛嘛",
        "今日份的水分补给（第 N 次）💧",
    ],
    "care_posture": [
        "坐了好久啦，起来伸个懒腰吧~",
        "行情不会跑的，主人起来走两步嘛",
        "鲸娘陪你站起来扭一扭~",
        "久坐警告！去阳台站一会儿好不好",
        "主人，腰要直直的才好看哦",
        "起来活动一下，鲸娘看着你的位置等你回来~",
        "伸懒腰时间到！左边弯弯右边弯弯~",
        "椅子之外还有大世界，去看看？",
    ],
    "care_eye": [
        "看看窗外最远的地方，休息一下眼睛👀",
        "20-20-20 法则：看 6 米外的东西 20 秒~",
        "眼睛干干的话就眨眨眼嘛，鲸娘教你",
        "盯屏幕太久了哦，让眼睛放个假",
        "望望远处，回来鲸娘给你讲行情~",
        "护眼时间！眨眼 10 次，开始！",
        "远处的风景免费，主人快看一眼",
        "鲸娘替你盯盘，主人看看绿色养养眼~",
    ],
    # 活跃度切换台词（v0.7.2）：切到哪一档就说哪组，act_quiet 必含「安静美少女」
    "act_quiet": [
        "嘘——我要变成安静美少女了，主人有事再叫我~",
        "（收起尾巴站好）安静美少女上线啦~",
        "进入安静模式，鲸娘会乖乖盯着金价不乱动~",
        "好累哦……鲸娘要变成安静美少女歇一歇~",
        "安静模式启动！只眨眼，不蹦跶~",
    ],
    "act_normal": [
        "回到标准节奏啦，鲸娘恢复正常~",
        "标准模式！该盯盘盯盘，该卖萌卖萌~",
        "节奏刚刚好，这是鲸娘最舒服的状态~",
        "（伸了个懒腰）标准模式，继续陪主人~",
    ],
    "act_lively": [
        "活力全开！鲸娘要开始蹦蹦跳跳啦~",
        "（尾巴摇得飞起）活泼模式启动！",
        "活泼模式！今天心情超好，一起加油呀~",
        "嘿嘿，动起来才舒服嘛~活泼模式！",
    ],
}

# 「大肥鱼」傲娇反应（v0.7.1）：连续叫会递进——反驳 → 生气 → 记仇；
# 摸摸头会哄好并重置。三层各 6 条，随机挑选。
FATTY_LINES: dict[int, list[str]] = {
    1: [  # 前两次：嘴硬反驳
        "谁、谁是大肥鱼啦！",
        "人家是鲸娘！鲸！娘！要叫对名字！",
        "哼，这叫丰腴，懂不懂呀~",
        "本娘可是一条有梦想的鲸鱼，不许乱叫！",
        "鲸鱼才不是鱼呢，主人没文化（骄傲）",
        "再看看把你眼馋的，哼，才不给你摸头",
    ],
    2: [  # 三到四次：生气
        "再叫大肥鱼，涨跌都不告诉主人了哼！",
        "哼！不理你了！（转身甩尾巴）",
        "气鼓鼓……气鼓鼓……（真的鼓起来了）",
        "主人好感度 -1 -1 -1！",
        "今天金价再好看也不夸主人了！",
        "哼哼哼！大坏蛋主人！",
    ],
    3: [  # 五次及以上：记仇但别扭
        "……（背过身去，尾巴却还在晃）",
        "大肥鱼就大肥鱼吧……才怪！哼！",
        "人家才没有一直在记仇呢……（小本本+1页）",
        "哼，主人去跟别的鲸鱼玩吧！",
        "（假装没听见，耳朵尖却红了）",
        "记仇小本本已经写到第三页了哦……",
    ],
}


def pick_fatty_line(count: int) -> str:
    """按「连续被叫大肥鱼」的次数挑台词：1-2 反驳 / 3-4 生气 / 5+ 记仇。"""
    tier = 1 if count <= 2 else (2 if count <= 4 else 3)
    return random.choice(FATTY_LINES[tier])


def _app_icon() -> QIcon:
    """应用统一图标（鲸鱼娘，v0.7.3）。

    为什么弹窗要显式 setWindowIcon：QDialog 只依赖「应用级/父窗口继承」时，
    实测标题栏会回落成 Qt 默认小鸟图标（父窗口 PetWindow 是无边框 Qt.Tool，
    图标没有真正落到窗口句柄上）。显式设置后 Windows 标题栏稳定显示鲸鱼娘。
    """
    path = asset_dir() / "icon.png"
    return QIcon(str(path)) if path.exists() else QIcon()


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

    def __init__(
        self,
        cfg: Config,
        service,
        history=None,
        auto_refresh: bool = True,
        notifier_history_path: Path | None = None,
    ):
        super().__init__()
        self.cfg = cfg
        self.service = service
        self.history = history
        self.metal = cfg.get("metal", "gold")
        self.unit = cfg.get("unit", "cny_g")
        self.snapshot: Snapshot | None = None
        self.state = "idle"
        self.fatty_count = 0  # 大肥鱼彩蛋计数：会话内累计，摸摸头哄好即重置
        self.frame_idx = 0
        self.frames: dict[str, list[QPixmap]] = {}
        self._drag_offset = None
        self._press_at = 0.0
        self._moved = False
        self._speak_timer = QTimer(self)
        self._speak_timer.setSingleShot(True)
        self._speak_timer.timeout.connect(self._restore_price)
        self._press_timer = QTimer(self)
        self._press_timer.setSingleShot(True)
        self._press_timer.timeout.connect(self._on_long_press)
        self._interact_timer = QTimer(self)
        self._interact_timer.setSingleShot(True)
        self._interact_timer.timeout.connect(self._end_interact)
        # v0.5.4：气泡临时消息（如「快捷方式创建成功」）到点后恢复行情
        self._bubble_restore_timer = QTimer(self)
        self._bubble_restore_timer.setSingleShot(True)
        self._bubble_restore_timer.timeout.connect(self._restore_bubble)
        self._long_pressed = False
        self._fail_streak = 0
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self.refresh)

        # v0.5：提醒规则、冷却与历史统一交给 Notifier（纯逻辑，可单测）
        # 落盘路径可注入：测试与多实例场景不应写到真实用户目录
        self.notifier = Notifier(
            cfg,
            notifier_history_path if notifier_history_path is not None else alert_history_path(),
        )
        # 默认托盘图标（数字图标渲染不出来时回退用）
        self._base_tray_icon: QIcon | None = None

        self._load_frames()
        self._build_ui()
        self._apply_window_flags()
        self._move_to_default()

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._tick_frame)
        self._activity_level = self.cfg.get("activity_level", "normal")
        self._sync_anim_timer()  # v0.7.2：安静档启动即定格，不启动帧循环

        # 关怀提醒（v0.7）：喝水 / 久坐 / 护眼，只走气泡
        self.care = CareScheduler(self.cfg)
        self.care_timer = QTimer(self)
        self.care_timer.timeout.connect(self._care_tick)
        self.care_timer.start(30 * 1000)  # 每半分钟查一次到点没有

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh)
        self._apply_interval()

        self.snapshot = self.service.load_cache()
        if self.snapshot and self.snapshot.ok:
            self._render(self.snapshot, silent=True)
        if auto_refresh:
            self.refresh()

        # AI 控制服务（v0.8）：默认开启，仅 127.0.0.1 + token，供 MCP 桥接进程调用
        self.control: ControlServer | None = None
        self._start_control()

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
        """恢复上次位置；若已不在任何屏幕内（换显示器/改分辨率），回落到主屏右下角。

        注意：本方法在 __init__ 里布局尚未定型，self.width()/height() 可能还是旧值。
        因此未命中历史位置时，用 QTimer.singleShot(0) 推迟到事件循环首帧，
        等 adjustSize 生效后再定位，否则会算错边距、把窗口顶到屏幕边缘。
        """
        pos = self.cfg.get("pos")
        if isinstance(pos, list) and len(pos) == 2:
            point = QPoint(int(pos[0]), int(pos[1]))
            for screen in QApplication.screens():
                if screen.availableGeometry().contains(point):
                    self.move(point)
                    return
        QTimer.singleShot(0, self._snap_to_corner)

    # ------------------------------------------------------------------ 定位
    def _window_size(self) -> tuple[int, int]:
        """窗口实际占位尺寸。

        布局定型前 width()/height() 可能还是旧值（见 _move_to_default 的说明），
        因此与 sizeHint() 取较大者，避免按过小的尺寸算边界、把窗口推出屏幕。
        """
        width = max(self.width(), self.sizeHint().width(), self.minimumSizeHint().width())
        height = max(self.height(), self.sizeHint().height(), self.minimumSizeHint().height())
        return width, height

    def _clamp_to_screen(self, x: int, y: int, screens_override=None) -> QPoint:
        """把窗口左上角坐标夹取到「离目标点最近的那块屏幕」的可视区内。

        多屏适配策略（拖动时调用，故必须便宜且无副作用）：
        1. 以窗口中心点判定归属屏幕——用左上角会在跨屏时抖动（窗口一半在左屏、
           一半在右屏时，左上角仍在左屏，夹取结果会把窗口拽回左屏，产生「卡住」感）。
           中心点判定让窗口能顺滑地跨过屏幕接缝。
        2. 归属屏幕取不到时（极端多屏布局）回落到当前 screen()，再回落到主屏。
        3. 单片屏幕比窗口还小时（罕见，如高 DPI 缩放），availableGeometry 无法容纳，
           此时退化为「保证左上角在屏幕内」，至少不让气泡和鲸娘跑到屏幕外不可见。

        screens_override 仅供测试注入屏幕列表，生产路径传 None 即用真实屏幕。
        """
        screens = QApplication.screens() if screens_override is None else list(screens_override)
        # R2-M-02：AI 控制服务 /move 允许任意量级的 int（如 10**12），而 QPoint 只吃
        # int32——先夹取再构造，否则 probe = QPoint(x, y) 抛 OverflowError 崩穿 UI 槽。
        # 合法坐标（屏幕像素级别）远在界内，此夹取对正常路径无感。
        x = max(-(2**31), min(2**31 - 1, int(x)))
        y = max(-(2**31), min(2**31 - 1, int(y)))
        screen = None
        probe = QPoint(x, y)
        for candidate in screens:
            if candidate.availableGeometry().contains(probe):
                screen = candidate
                break
        if screen is None:
            screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return QPoint(x, y)

        geo = screen.availableGeometry()
        width, height = self._window_size()

        # 屏幕容纳不下窗口：退化为「左上角入屏」，避免把坐标抬到屏幕外
        if width > geo.width() or height > geo.height():
            return QPoint(
                max(geo.left(), min(x, geo.right())),
                max(geo.top(), min(y, geo.bottom())),
            )

        clamped_x = max(geo.left(), min(x, geo.right() - width + 1))
        clamped_y = max(geo.top(), min(y, geo.bottom() - height + 1))
        return QPoint(clamped_x, clamped_y)

    def _snap_to_corner(self, margin: int = 24) -> None:
        """把窗口摆到主屏右下角，留出 margin 边距，并夹取在可视区内。"""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        width, height = self._window_size()
        x = geo.right() - width - margin
        y = geo.bottom() - height - margin
        point = self._clamp_to_screen(x, y)
        self.move(point)

    def _apply_interval(self) -> None:
        self.refresh_timer.start(int(self.cfg.get("interval_sec", 60)) * 1000)

    # ------------------------------------------------------------------ 动画
    def _anim_interval(self, state: str) -> int:
        """按「动作活跃度」换算该状态的帧间隔（毫秒）。

        quiet = 安静（系数 0，完全静止） / normal = 标准 / lively = 活泼(×0.7)。
        返回 0 表示该档位要求静止：帧循环应停住，只显示第 0 帧（v0.7.2）。
        """
        factor = ACTIVITY_LEVELS.get(self.cfg.get("activity_level", "normal"), ("", 1.0))[1]
        return round(STATES[state][1] * factor)

    def _sync_anim_timer(self) -> None:
        """按当前状态与活跃度决定动画 timer 走或停（单点收口，v0.7.2）。

        安静档（间隔 0）：停表并定格在第 0 帧——「安静就是不动」；
        其他档位：按帧间隔开跑（已在跑则只更新节奏）。
        """
        interval = self._anim_interval(self.state)
        if interval <= 0:
            self.anim_timer.stop()
            self.frame_idx = 0
            self.pet.setPixmap(self._current_pixmap())
        else:
            self.anim_timer.setInterval(interval)
            if not self.anim_timer.isActive():
                self.anim_timer.start()

    def _apply_activity(self) -> None:
        """活跃度变更后立即生效，并说一句对应档位的台词（v0.7.2）。"""
        level = self.cfg.get("activity_level", "normal")
        if level == self._activity_level:
            return  # 档位没变，不重排也不说话
        was = self._activity_level
        self._activity_level = level
        self._sync_anim_timer()
        self.speak(f"act_{level}")
        logging.getLogger("gold_pet").info("活跃度切换：%s -> %s", was, level)

    def _set_state(self, state: str) -> None:
        if state not in self.frames:
            state = "idle"
        if state == self.state:
            return
        self.state = state
        self.frame_idx = 0
        self._sync_anim_timer()  # v0.7.2：安静档下换状态也是定格，不播循环
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
            # P2-02：旧 worker 以 window 为 parent，只 disconnect 不销毁的话
            # QThread 对象随每次刷新永久累积（60s 档约 1440 个/天）。能走到这里
            # 说明 isRunning() 已为 False（线程已结束），deleteLater 交给事件
            # 循环在主线程回收，不存在销毁运行中线程的竞态。
            worker.deleteLater()
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
        show_bubble = bool(self.cfg.get("show_bubble", True))

        if quote is None:
            self._set_state("offline")
            if not show_bubble:
                return
            cache = self.service.load_cache()
            q = cache.price_of(self.metal, self.unit) if cache else None
            if q is not None:
                self.bubble.setText(render_cache_html(metal_name, q, updated_at=cache.updated_at))
            else:
                self.bubble.setText("行情获取失败")
            return

        change = quote.change
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
        if show_bubble:
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
            self._update_tray_icon(quote)
        if not silent and snap.ok:
            base = quote if quote.unit.startswith("元") else None
            if base is None and self.snapshot is not None:
                base = self.snapshot.price_of(self.metal, "cny_g")
            self._dispatch_alerts(self.notifier.evaluate(quote, metal_name, base_quote=base))

    def _dispatch_alerts(self, alerts: list[Alert]) -> None:
        """把规则层产出的提醒落到界面：气泡高亮 + 鲸娘说话 + 系统通知。

        勿扰时段内只静音系统通知，气泡与台词照常（用户可在设置里关闭勿扰）。
        """
        if not alerts:
            return
        self.notifier.record(alerts)
        quiet = self.notifier.in_quiet_hours()
        tray = self.tray_icon()
        for alert in alerts:
            self.bubble.setStyleSheet(ALERT_STYLE)
            QTimer.singleShot(8000, lambda: self.bubble.setStyleSheet(BUBBLE_STYLE))
            # 台词方向：涨/新高 → high，跌/新低 → low
            self.speak("high" if alert.side in ("up", "high") else "low")
            if tray is not None and not quiet:
                tray.showMessage(alert.title, alert.body, QSystemTrayIcon.Information, 8000)
            logging.getLogger("gold_pet").info("触发提醒[%s]: %s", alert.kind, alert.body)
        if quiet:
            logging.getLogger("gold_pet").info("勿扰时段，已静音 %d 条系统通知", len(alerts))

    def _update_tray_icon(self, quote) -> None:
        """托盘图标按当前价格渲染数字；拿不到可显示的数字时退回默认图标。"""
        tray = self.tray_icon()
        if tray is None:
            return
        if not bool(self.cfg.get("tray_show_price", True)):
            if self._base_tray_icon is not None:
                tray.setIcon(self._base_tray_icon)
            return
        from .tray_icon import render_fallback_icon, render_price_icon

        icon = render_price_icon(quote.price, quote.change)
        tray.setIcon(icon if icon is not None else render_fallback_icon(self._base_tray_icon))

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
        # v0.5：按配置的时间范围过滤（1 小时 / 今日 / 全部）
        range_key = self.cfg.get("spark_range", "1h")
        values = filter_series(series, range_key)
        if not values:  # 范围内无点时退回原始序列末尾，避免图空着
            # P2 残留修复：p 可能为数值字符串（_valid_point 允许），转 float 防 Sparkline.min() TypeError
            values = [_safe_float(p.get("p", 0.0), 0.0) for p in series][-60:]
        self.spark.set_series(values, color)

    # ------------------------------------------------------------------ 台词
    def speak(self, mood: str | None = None) -> None:
        """气泡里说一句话，4 秒后恢复价格显示。"""
        mood = mood or self.state if self.state in LINES else "idle"
        if mood not in LINES:
            mood = "idle"
        self.bubble.setText(render_line_html(random.choice(LINES[mood])))
        self._speak_timer.start(4000)

    # ------------------------------------------------------- 关怀提醒（v0.7）
    def _care_tick(self) -> None:
        """每半分钟查一次：到点的关怀提醒就让鲸娘说一句。

        勿扰时段内不出声（计时保留，勿扰结束后到点的会立即补上）。
        同一时刻到期多条时只说优先级最高的一条，其余顺延 90 秒。
        """
        dnd = bool(self.cfg.get("dnd_enabled", False)) and in_quiet_hours(
            None, self.cfg.get("dnd_start", "23:00"), self.cfg.get("dnd_end", "08:00")
        )
        kind = self.care.due(time.time(), dnd_active=dnd)
        if kind is None:
            return
        for other in CARE_KINDS:
            if other != kind:
                self.care.defer(other, time.time())
        self.care.mark(kind, time.time())
        self.speak(kind)
        logging.getLogger("gold_pet").info("关怀提醒已播报: %s", kind)

    def _restore_price(self) -> None:
        if self.snapshot is not None:
            self._render(self.snapshot, silent=True)

    # ------------------------------------------------------------------ 引导
    def welcome(self) -> None:
        """首次运行引导：鲸娘自我介绍 + 托盘气泡说明交互方式。

        由 main.py 在检测到首次启动（settings.json 不存在）时调用。
        无托盘环境下只说话，不弹气泡通知。
        """
        self.speak("welcome")
        tray = self.tray_icon()
        if tray is not None and tray.isSystemTrayAvailable():
            tray.showMessage(
                "黄金桌宠 · 鲸娘来啦",
                "左键单击 = 摸摸头 · 双击 = 立即刷新 · 拖动 = 换位置 · 右键 = 菜单",
                QSystemTrayIcon.Information,
                10000,
            )
        logging.getLogger("gold_pet").info("首次运行引导已展示")

    # ------------------------------------------------------------------ 交互
    def tray_icon(self):
        return getattr(self, "_tray", None)

    def attach_tray(self, tray: QSystemTrayIcon) -> None:
        self._tray = tray

    # ------------------------------------------------------- 摸头 / 撒娇
    def pat(self) -> None:
        """摸头：眯眼笑 + 台词，1.5 秒后回到行情状态。

        摸头会哄好「被叫大肥鱼」的傲娇：生气计数归零（v0.7.1）。
        """
        if self.fatty_count > 0:
            self.fatty_count = 0
        self.speak("pat")
        self._play_state("pat", 1500)

    def shy(self) -> None:
        """撒娇：眼神飘开 + 台词，2.5 秒后回到行情状态。"""
        self.speak("shy")
        self._play_state("shy", 2500)

    # -------------------------------------------------- 「大肥鱼」傲娇彩蛋
    def tease_fatty(self) -> None:
        """叫她「大肥鱼」：傲娇递进反应，连续叫会越来越生气，摸头可哄好。

        会话内计数（不持久化）——每次启动都是新的原谅。
        """
        self.fatty_count += 1
        self.bubble.setText(render_line_html(pick_fatty_line(self.fatty_count)))
        self._speak_timer.start(4000)
        self._play_state("shy", 2500)  # 恼怒害羞的表情最贴切
        logging.getLogger("gold_pet").info("触发大肥鱼彩蛋（连续第 %d 次）", self.fatty_count)

    def _play_state(self, state: str, ms: int) -> None:
        self._set_state(state)
        self._interact_timer.start(ms)

    def _on_long_press(self) -> None:
        """长按 600ms 且期间未移动 = 撒娇；已开始拖动则不触发。"""
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
        # v0.5.6：换了品种，上一品种的水位/档位不再有意义
        self.notifier.reset_state()
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
            # 一旦开始拖动，就取消长按计时，避免拖动中误触发撒娇
            if not self._moved:
                self._moved = True
                self._press_timer.stop()
            # 关键：拖动必须夹取在屏幕可视区内。此前直接 move()，
            # 贴边时窗口（高 239px，气泡在鲸娘上方）会被推出屏幕，
            # 表现为气泡与鲸娘被裁掉一半的「卡半边」。
            target = event.globalPosition().toPoint() - self._drag_offset
            self.move(self._clamp_to_screen(target.x(), target.y()))
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
        """右键菜单：与托盘菜单共用 build_menu()，避免两套代码漂移。"""
        build_menu(self).exec(event.globalPos())
        event.accept()

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
        # v0.5.6：记录改动前的提醒规则，规则变了就重置去重状态，
        # 否则旧状态（如按旧阈值算出的档位）会与新规则对不上。
        before = {key: self.cfg.get(key) for key in RULE_CONFIG_KEYS}
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            self.metal = self.cfg.get("metal", "gold")
            self.unit = self.cfg.get("unit", "cny_g")
            self._apply_window_flags()
            self._apply_interval()
            self._apply_activity()  # v0.7：活跃度立即生效，不用重启
            self._apply_control_setting()  # v0.8：AI 控制开关立即启停
            self.care.reset(time.time())  # v0.7：关怀提醒按新配置重排
            self.bubble.setVisible(bool(self.cfg.get("show_bubble", True)))
            if before != {key: self.cfg.get(key) for key in RULE_CONFIG_KEYS}:
                self.notifier.reset_state()
                logging.getLogger("gold_pet").info("提醒规则已变更，已重置提醒去重状态")
            self.show()
            self.refresh()

    def open_about(self) -> None:
        """关于窗口：版本、数据来源、免责声明、许可。"""
        AboutDialog(self).exec()

    # -------------------------------------------------- 快捷方式（v0.5.4）
    def create_shortcuts(self) -> None:
        """一键创建桌面 + 开始菜单快捷方式，并给用户明确反馈。"""
        icon = asset_dir() / "icon.ico"
        results = shortcut.create_many(
            ["desktop", "startmenu"], icon=icon if icon.exists() else None
        )
        made = [name for name, ok in results.items() if ok]
        if made:
            labels = {"desktop": "桌面", "startmenu": "开始菜单"}
            self.bubble_say("已把鲸娘放到：" + "、".join(labels[m] for m in made) + " 🎉", 4000)
            logging.getLogger("gold_pet").info("快捷方式创建成功：%s", made)
        else:
            self.bubble_say("快捷方式创建失败，请检查权限 😢", 4000)
            logging.getLogger("gold_pet").warning("快捷方式创建失败：%s", results)

    def open_install_folder(self) -> None:
        """打开程序所在文件夹，方便用户自己拖快捷方式或查看文件。"""
        target = shortcut._target_path()
        if target is None:
            return
        folder = target.parent
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        except Exception as exc:  # pragma: no cover - 依赖系统外壳
            logging.getLogger("gold_pet").warning("打开文件夹失败：%s", exc)

    def bubble_say(self, text: str, ms: int = 3000) -> None:
        """临时在气泡里说一句话，ms 后恢复行情显示。

        与 speak() 的区别：speak 走的是「鲸娘台词」体系（PAT_LINES 等），
        这里用于系统级反馈（创建成功/失败），不占用台词池。
        """
        self.bubble.setText(text)
        self._bubble_restore_timer.start(ms)

    def _restore_bubble(self) -> None:
        """恢复气泡的行情内容。"""
        if self.snapshot is not None:
            self._render(self.snapshot, silent=True)

    # ------------------------------------------------------- AI 控制（v0.8）
    def _start_control(self) -> None:
        """按配置启动 AI 控制服务（幂等：已在跑则不动）。"""
        if not bool(self.cfg.get("control_enabled", True)):
            logging.getLogger("gold_pet").info("AI 控制服务未启用（配置关闭）")
            return
        if self.control is not None and self.control.isRunning():
            return
        control = ControlServer(state_reader=self._control_state)
        control.action_received.connect(self._handle_control)
        # R3：QThread 启动失败（线程资源耗尽等）在 PetWindow 构造路径上会直接
        # 炸穿 init。控制服务是可选能力，启动失败只降级不崩。
        try:
            control.start()
        except (RuntimeError, OSError):
            logging.getLogger("gold_pet").warning(
                "AI 控制服务线程启动失败，本次不提供控制服务", exc_info=True
            )
            self.control = None
            return
        self.control = control

    def _apply_control_setting(self) -> None:
        """设置面板保存后按需启停 AI 控制服务（v0.8）。"""
        enabled = bool(self.cfg.get("control_enabled", True))
        running = self.control is not None and self.control.isRunning()
        if enabled and not running:
            self._start_control()
        elif not enabled and running:
            self._stop_control()

    def _stop_control(self) -> None:
        """停掉 AI 控制服务并清理发现文件（退出路径与设置开关共用）。"""
        if self.control is None:
            return
        self.control.stop()
        self.control.wait(5000)
        self.control = None
        logging.getLogger("gold_pet").info("AI 控制服务已停止")

    def _control_state(self) -> dict:
        """HTTP 线程读取的只读状态快照。

        只读纯 Python 属性（str/int，GIL 原子），绝不调用 QWidget 方法
        （Qt 界面对象只允许主线程触碰）。
        """
        return {
            "app": "gold-pet",
            "version": __version__,
            "state": self.state,
            "activity_level": self._activity_level,
            "fatty_count": self.fatty_count,
        }

    def _handle_control(self, action: dict) -> None:
        """AI 控制动作统一入口（UI 线程槽，由控制服务经信号转发）。"""
        name = action.get("action")
        if name == "say":
            text = action.get("text")
            if isinstance(text, str) and text.strip():
                self.bubble_say(render_line_html(text.strip()), 4000)
            else:
                self.speak(action.get("mood"))
        elif name == "play":
            state = action.get("state")
            if state in STATES:
                self._play_state(state, 2500)
        elif name == "pat":
            self.pat()
        elif name == "tease":
            self.tease_fatty()
        elif name == "move":
            self._control_move(action)
        logging.getLogger("gold_pet").info("AI 控制动作：%s", action)

    def _control_move(self, action: dict) -> None:
        """按 AI 指令挪动窗口：dx/dy 相对 或 x/y 绝对，夹取回屏幕内。"""
        # R-monkey 审查：动作 dict 经 HTTP/MCP 边界进来，数值字段可能带
        # 字符串/对象等脏形态——int() 的 ValueError/TypeError/OverflowError
        # 若未接住会崩穿 UI 槽（与 R2-M-02 同族残余面），静默忽略本条指令。
        try:
            if "dx" in action and "dy" in action:
                x = self.x() + int(action["dx"])
                y = self.y() + int(action["dy"])
            else:
                x, y = int(action["x"]), int(action["y"])
        except (TypeError, ValueError, OverflowError, KeyError):
            return
        point = self._clamp_to_screen(x, y)
        self.move(point)
        self.cfg.set("pos", [point.x(), point.y()])
        self.cfg.save()

    def open_alert_history(self) -> None:
        """提醒历史窗口（v0.5 提醒中心）。"""
        AlertHistoryDialog(self.notifier, self).exec()

    def set_spark_range(self, range_key: str) -> None:
        """切换走势图时间范围并立即重绘（v0.5）。"""
        if range_key not in SPARK_RANGE_NAMES:
            return
        self.cfg.set("spark_range", range_key)
        self.cfg.save()
        if self.snapshot is not None:
            self._render(self.snapshot, silent=True)
        logging.getLogger("gold_pet").info(
            "走势图范围切换为 %s", SPARK_RANGE_NAMES.get(range_key, range_key)
        )

    def quit_app(self) -> None:
        self.cfg.set("pos", [self.x(), self.y()])
        self.cfg.save()
        self._stop_control()  # v0.8：退出前停 AI 控制服务 + 删发现文件
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
            self._stop_control()  # v0.8：退出前停 AI 控制服务 + 删发现文件
            event.accept()
            QApplication.quit()


class SettingsDialog(QDialog):
    """设置面板。"""

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("黄金桌宠 · 设置")
        self.setWindowIcon(_app_icon())  # v0.7.3：统一鲸鱼娘标题栏图标
        self.setModal(True)
        # 限制宽度，避免表单被拉成满屏（QFormLayout 会按最长行撑开窗口）
        self.setMinimumWidth(380)
        self.setMaximumWidth(520)

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

        # v0.5.6：新高新低最小步进，避免连续微微刷新高时刷屏
        self.extreme_step_spin = QDoubleSpinBox()
        self.extreme_step_spin.setRange(0.0, 5.0)
        self.extreme_step_spin.setSingleStep(0.1)
        self.extreme_step_spin.setDecimals(1)
        self.extreme_step_spin.setSuffix(" %")
        self.extreme_step_spin.setToolTip(
            "新价位比上次提醒时再高出这么多才算「创新高」；0 表示任何新高都提醒"
        )
        self.extreme_step_spin.setValue(float(cfg.get("alert_extreme_step_pct", 0.1)))

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

        # v0.5.4：快捷方式管理（仅 Windows 打包版有意义）
        self.desktop_link_check = QCheckBox("桌面快捷方式")
        self.desktop_link_check.setChecked(shortcut.is_created("desktop"))
        self.startmenu_link_check = QCheckBox("开始菜单快捷方式")
        self.startmenu_link_check.setChecked(shortcut.is_created("startmenu"))
        self._link_supported = shortcut.is_supported()

        # ---- v0.5 提醒中心：三类提醒独立开关 ----
        self.pct_enabled_check = QCheckBox("启用涨跌幅提醒")
        self.pct_enabled_check.setChecked(bool(cfg.get("alert_pct_enabled", True)))

        self.target_enabled_check = QCheckBox("启用到价提醒")
        self.target_enabled_check.setChecked(bool(cfg.get("alert_target_enabled", True)))

        self.extreme_enabled_check = QCheckBox("启用新高新低提醒")
        self.extreme_enabled_check.setChecked(bool(cfg.get("alert_extreme_enabled", True)))

        # ---- v0.5 勿扰模式 ----
        self.dnd_check = QCheckBox("勿扰模式（时段内不弹系统通知）")
        self.dnd_check.setChecked(bool(cfg.get("dnd_enabled", False)))

        self.dnd_start_spin = QTimeEdit()
        self.dnd_start_spin.setDisplayFormat("HH:mm")
        self.dnd_start_spin.setTime(_parse_qtime(cfg.get("dnd_start", "23:00")))

        self.dnd_end_spin = QTimeEdit()
        self.dnd_end_spin.setDisplayFormat("HH:mm")
        self.dnd_end_spin.setTime(_parse_qtime(cfg.get("dnd_end", "08:00")))

        # ---- v0.5 走势范围与托盘数字 ----
        self.spark_range_combo = QComboBox()
        for key, label in SPARK_RANGE_NAMES.items():
            self.spark_range_combo.addItem(label, key)
        idx_range = self.spark_range_combo.findData(cfg.get("spark_range", "1h"))
        self.spark_range_combo.setCurrentIndex(max(0, idx_range))

        self.tray_price_check = QCheckBox("托盘图标显示价格数字")
        self.tray_price_check.setChecked(bool(cfg.get("tray_show_price", True)))

        # ---- v0.7 动作活跃度 ----
        self.activity_combo = QComboBox()
        for key, (label, _factor) in ACTIVITY_LEVELS.items():
            self.activity_combo.addItem(label, key)
        idx_act = self.activity_combo.findData(cfg.get("activity_level", "normal"))
        self.activity_combo.setCurrentIndex(max(0, idx_act))

        # ---- v0.7 关怀提醒：喝水 / 久坐 / 护眼 ----
        self.drink_check = QCheckBox("喝水提醒")
        self.drink_check.setChecked(bool(cfg.get("care_drink_enabled", True)))
        self.drink_combo = QComboBox()
        for minutes in CARE_INTERVAL_CHOICES:
            self.drink_combo.addItem(f"每 {minutes} 分钟", minutes)
        drink_cur = int(cfg.get("care_drink_interval_min", 60))
        self.drink_combo.setCurrentIndex(
            CARE_INTERVAL_CHOICES.index(drink_cur) if drink_cur in CARE_INTERVAL_CHOICES else 2
        )

        self.posture_check = QCheckBox("久坐提醒（每 90 分钟）")
        self.posture_check.setChecked(bool(cfg.get("care_posture_enabled", False)))

        self.eye_check = QCheckBox("护眼提醒（每 45 分钟）")
        self.eye_check.setChecked(bool(cfg.get("care_eye_enabled", False)))

        # ---- v0.8 AI 控制服务 ----
        self.control_check = QCheckBox("启用 AI 控制服务")
        self.control_check.setChecked(bool(cfg.get("control_enabled", True)))

        form = QFormLayout()
        form.addRow("关注品种", self.metal_combo)
        form.addRow("默认单位", self.unit_combo)
        form.addRow("刷新间隔", self.interval_combo)
        form.addRow("动作活跃度", self.activity_combo)
        form.addRow("不透明度", self.opacity_slider)
        form.addRow("", self.top_check)
        form.addRow("", self.bubble_check)
        form.addRow("", self.spark_check)
        form.addRow("走势图范围", self.spark_range_combo)
        form.addRow("", self.tray_price_check)
        form.addRow("", self.autostart_check)
        if self._link_supported:
            # 快捷方式仅在 Windows 打包版有实际作用，其他环境不显示以免困惑
            form.addRow("", self.desktop_link_check)
            form.addRow("", self.startmenu_link_check)

        alert_form = QFormLayout()
        alert_form.addRow("涨跌提醒阈值", self.alert_spin)
        alert_form.addRow("重复提醒防抖", self.cooldown_spin)
        alert_form.addRow("新高新低步进", self.extreme_step_spin)
        alert_form.addRow("到价提醒 ≥（0 关闭）", self.target_high_spin)
        alert_form.addRow("到价提醒 ≤（0 关闭）", self.target_low_spin)
        alert_form.addRow("", self.pct_enabled_check)
        alert_form.addRow("", self.target_enabled_check)
        alert_form.addRow("", self.extreme_enabled_check)
        # v0.5.6：把去重语义直接写在界面上，避免用户误以为「价格没动怎么还在弹」
        alert_form.addRow(
            "",
            _hint_label(
                "提醒只在「状态变化」时出现：价格横盘不会重复弹窗。\n"
                "涨跌幅按阈值分档，跨入更高档位才再提醒一次；\n"
                "新高新低要严格刷新当日水位才算；到价提醒回到目标价外后重新武装。"
            ),
        )

        dnd_form = QFormLayout()
        dnd_form.addRow("", self.dnd_check)
        dnd_form.addRow("勿扰开始", self.dnd_start_spin)
        dnd_form.addRow("勿扰结束", self.dnd_end_spin)

        # v0.7 关怀提醒：陪伴类气泡，不走系统通知、不进提醒中心
        care_form = QFormLayout()
        care_form.addRow("喝水提醒", self.drink_combo)
        care_form.addRow("", self.drink_check)
        care_form.addRow("", self.posture_check)
        care_form.addRow("", self.eye_check)
        care_form.addRow(
            "",
            _hint_label(
                "到点时鲸娘会用气泡说一句话（约 4 秒后自动消失），\n"
                "不弹系统通知、不进提醒中心；勿扰时段内不会出声。"
            ),
        )

        # v0.8 AI 控制服务：本机 MCP 桥接专用，外部机器无法连接
        control_form = QFormLayout()
        control_form.addRow("", self.control_check)
        control_form.addRow(
            "",
            _hint_label(
                "开启后 AI（如 WorkBuddy / Cursor 的 MCP 工具）可以操控鲸娘：\n"
                "让她说话、做动作、摸头、挪位置。服务只监听本机 127.0.0.1\n"
                "随机端口，且需要随机 token 才能访问，外部机器无法连接。\n"
                "接入信息文件：GoldPet 数据目录下的 control.json（保存后生效）。"
            ),
        )

        ok = QPushButton("保存")
        cancel = QPushButton("取消")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)

        # v0.5：设置项变多，内容放进滚动区，避免窗口被撑满屏幕
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.addWidget(_section_label("基本"))
        content_layout.addLayout(form)
        content_layout.addWidget(_section_label("提醒中心"))
        content_layout.addLayout(alert_form)
        content_layout.addWidget(_section_label("关怀提醒"))
        content_layout.addLayout(care_form)
        content_layout.addWidget(_section_label("勿扰模式"))
        content_layout.addLayout(dnd_form)
        content_layout.addWidget(_section_label("AI 控制"))
        content_layout.addLayout(control_form)
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addSpacing(4)
        layout.addLayout(buttons)

        # 定好尺寸后再居中到父窗口/主屏，避免出现在屏幕外或贴边
        self.resize(430, 620)
        self._center_on_parent(parent)

    def _center_on_parent(self, parent) -> None:
        """把对话框摆到父窗口（或主屏）中央；父窗口可能在屏幕边缘，需夹取到可视区内。"""
        screen = None
        if parent is not None:
            screen = parent.screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        width = min(self.width(), geo.width())
        height = min(self.height(), geo.height())
        self.resize(width, height)

        if parent is not None and parent.isVisible():
            anchor = parent.frameGeometry()
        else:
            anchor = geo
        x = anchor.center().x() - width // 2
        y = anchor.center().y() - height // 2
        # 夹取到屏幕可视范围内，保证标题栏与按钮都点得到
        x = max(geo.left(), min(x, geo.right() - width))
        y = max(geo.top(), min(y, geo.bottom() - height))
        self.move(x, y)

    def accept(self):  # noqa: D102
        self.cfg.set("metal", self.metal_combo.currentData())
        self.cfg.set("unit", self.unit_combo.currentData())
        self.cfg.set("interval_sec", int(self.interval_combo.currentData()))
        self.cfg.set("alert_pct", float(self.alert_spin.value()))
        self.cfg.set("alert_cooldown_sec", int(self.cooldown_spin.value()) * 60)
        self.cfg.set("alert_extreme_step_pct", float(self.extreme_step_spin.value()))
        self.cfg.set("target_high", float(self.target_high_spin.value()))
        self.cfg.set("target_low", float(self.target_low_spin.value()))
        self.cfg.set("opacity", self.opacity_slider.value() / 100.0)
        self.cfg.set("always_on_top", self.top_check.isChecked())
        self.cfg.set("show_bubble", self.bubble_check.isChecked())
        self.cfg.set("show_spark", self.spark_check.isChecked())
        self.cfg.set("autostart", self.autostart_check.isChecked())
        # v0.5
        self.cfg.set("alert_pct_enabled", self.pct_enabled_check.isChecked())
        self.cfg.set("alert_target_enabled", self.target_enabled_check.isChecked())
        self.cfg.set("alert_extreme_enabled", self.extreme_enabled_check.isChecked())
        self.cfg.set("dnd_enabled", self.dnd_check.isChecked())
        self.cfg.set("dnd_start", self.dnd_start_spin.time().toString("HH:mm"))
        self.cfg.set("dnd_end", self.dnd_end_spin.time().toString("HH:mm"))
        self.cfg.set("spark_range", self.spark_range_combo.currentData())
        self.cfg.set("tray_show_price", self.tray_price_check.isChecked())
        # v0.7 活跃度 + 关怀提醒
        self.cfg.set("activity_level", self.activity_combo.currentData())
        self.cfg.set("care_drink_enabled", self.drink_check.isChecked())
        self.cfg.set("care_drink_interval_min", int(self.drink_combo.currentData()))
        self.cfg.set("care_posture_enabled", self.posture_check.isChecked())
        self.cfg.set("care_eye_enabled", self.eye_check.isChecked())
        # v0.8 AI 控制服务
        self.cfg.set("control_enabled", self.control_check.isChecked())
        autostart.set_enabled(self.autostart_check.isChecked())
        # v0.5.4：按勾选状态增删快捷方式（幂等，重复创建会覆盖）
        if self._link_supported:
            icon = asset_dir() / "icon.ico"
            icon_arg = icon if icon.exists() else None
            for where, check in (
                ("desktop", self.desktop_link_check),
                ("startmenu", self.startmenu_link_check),
            ):
                if check.isChecked():
                    shortcut.create(where, icon=icon_arg)
                else:
                    shortcut.remove(where)
        self.cfg.save()
        super().accept()


def _section_label(text: str) -> QLabel:
    """设置面板里的分组标题。"""
    label = QLabel(text)
    label.setStyleSheet("font-weight: 600; color: #b8860b; padding-top: 6px;")
    return label


def _hint_label(text: str) -> QLabel:
    """设置面板里的灰色说明文字（自动换行，不抢焦点）。"""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #8a8a8a; font-size: 11px; padding: 2px 0 0 0;")
    return label


def _parse_qtime(text: str) -> QTime:
    """把 "HH:MM" 解析为 QTime；非法则回落 00:00。"""
    from .notifier import parse_hhmm

    minutes = parse_hhmm(text)
    if minutes is None:
        return QTime(0, 0)
    return QTime(minutes // 60, minutes % 60)


class AlertHistoryDialog(QDialog):
    """提醒历史窗口（v0.5 提醒中心）。"""

    def __init__(self, notifier: Notifier, parent=None):
        super().__init__(parent)
        self.notifier = notifier
        self.setWindowTitle("提醒历史")
        self.setWindowIcon(_app_icon())  # v0.7.3：统一鲸鱼娘标题栏图标
        self.setModal(True)
        self.setMinimumSize(420, 320)

        self.list = QListWidget()
        self._reload()

        clear = QPushButton("清空历史")
        clear.clicked.connect(self._clear)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        layout.addLayout(buttons)

    def _reload(self) -> None:
        self.list.clear()
        items = self.notifier.history()
        if not items:
            self.list.addItem("暂无提醒记录")
            return
        for alert in items:
            clock = time.strftime("%m-%d %H:%M:%S", time.localtime(alert.at))
            self.list.addItem(f"[{clock}] {alert.kind_name}｜{alert.body}")

    def _clear(self) -> None:
        self.notifier.clear_history()
        # v0.5.6：清空历史的同时重置去重状态，用户不必重启就能重新收到提醒
        self.notifier.reset_state()
        self._reload()


class AboutDialog(QDialog):
    """关于窗口：版本、数据来源、免责声明、开源许可。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("关于 黄金桌宠")
        self.setWindowIcon(_app_icon())  # v0.7.3：统一鲸鱼娘标题栏图标
        self.setModal(True)
        self.setMinimumWidth(360)

        title = QLabel('<div style="font-size:16px;font-weight:600">🐋 黄金桌宠</div>')
        version = QLabel(f"版本 {__version__}")
        version.setStyleSheet("color: #666;")

        body = QLabel(
            "<div style='font-size:12px; line-height:1.7'>"
            "一只住在桌面上的「鲸娘」，实时盯着金价：<br>"
            "涨了笑、跌了哭，点一下就能看到最新价格。<br><br>"
            "<b>数据来源</b><br>"
            "· 国内金价：新浪财经 <code>gds_AUTD</code>（上金所 Au T+D）<br>"
            "· 国际金价：新浪财经 <code>hf_XAU</code>（伦敦金）<br>"
            "· 汇率：<code>fx_susdcny</code>，"
            "换算 <code>元/克 = 美元/盎司 ÷ 31.1034768 × 汇率</code><br>"
            "均为免密钥公开接口，可能存在延迟或中断。<br><br>"
            "<b>免责声明</b><br>"
            "行情数据仅供参考，<b>不构成任何投资建议</b>。<br>"
            "本程序不采集任何个人信息，配置与历史仅存于本机。<br><br>"
            "<b>开源许可</b><br>MIT License · "
            "<a href='https://cnb.cool/TJ-coding/gold-pet'>cnb.cool/TJ-coding/gold-pet</a><br><br>"
            "<b>形象素材致谢</b><br>"
            "鲸鱼娘动画来自 "
            "<a href='https://github.com/aceice01/dsh-whale-pet'>aceice01/dsh-whale-pet</a>"
            "（社区同人素材，版权归原作者），"
            "按其非商业使用许可分发，本项目为免费非商业用途。"
            "</div>"
        )
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(Qt.TextBrowserInteraction)

        ok = QPushButton("知道了")
        ok.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(ok)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(version)
        layout.addSpacing(4)
        layout.addWidget(body)
        layout.addSpacing(8)
        layout.addLayout(buttons)


class FirstRunDialog(QDialog):
    """首次运行引导：帮小白用户把「入口」放到找得到的地方。

    背景：桌宠用 Qt.Tool 不进任务栏（产品设计如此），用户关掉窗口后
    无处可寻，下次只能用「最近使用」翻出来或重新下载。
    本对话框用一句人话问清楚，勾选即建快捷方式，不静默往桌面丢图标。

    仅在**打包后的 exe** 运行时调用；源码运行会跳过（见 main.py）。
    """

    def __init__(self, parent=None, has_tray: bool = True):
        super().__init__(parent)
        self.setWindowTitle("欢迎使用 黄金桌宠")
        self.setWindowIcon(_app_icon())  # v0.7.3：统一鲸鱼娘标题栏图标
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMaximumWidth(520)

        title = QLabel('<div style="font-size:16px;font-weight:600">🐋 鲸娘来啦～</div>')
        intro = QLabel(
            "<div style='font-size:12px;line-height:1.7'>"
            "鲸娘会一直<b>待在桌面右下角</b>陪你盯金价，"
            "涨了笑、跌了哭，点一下就能切单位。<br><br>"
            + (
                "关掉窗口后她不会消失，而是<b>回到系统托盘的 ^ 里</b>"
                "（时钟旁边那个小箭头），随时能叫她出来。"
                if has_tray
                else "关掉窗口她就退出了，想再见到她请重新打开本程序。"
            )
            + "</div>"
        )
        intro.setWordWrap(True)

        hint = QLabel(
            '<div style="font-size:12px;color:#666">'
            "为了让您<b>下次也能方便地找到她</b>，建议添加到下面这些位置：</div>"
        )
        hint.setWordWrap(True)

        self.desktop_check = QCheckBox("在桌面创建快捷方式（推荐）")
        self.desktop_check.setChecked(True)
        self.startmenu_check = QCheckBox("添加到开始菜单")
        self.startmenu_check.setChecked(True)
        self.autostart_check = QCheckBox("开机自动启动（每次开机自动陪您）")
        self.autostart_check.setChecked(False)

        # 托盘不可用时，开头就要提醒用户「关窗即退出」，避免以为还在后台跑
        ok = QPushButton("好呀，就这么办")
        ok.setDefault(True)
        skip = QPushButton("暂时不用")

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addSpacing(2)
        layout.addWidget(intro)
        layout.addSpacing(8)
        layout.addWidget(hint)
        layout.addSpacing(2)
        layout.addWidget(self.desktop_check)
        layout.addWidget(self.startmenu_check)
        layout.addWidget(self.autostart_check)
        layout.addSpacing(8)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(skip)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

        ok.clicked.connect(self.accept)
        skip.clicked.connect(self.reject)

        self.resize(440, 340)
        _center_on_screen(self, parent)

    def wants_desktop(self) -> bool:
        return self.desktop_check.isChecked()

    def wants_startmenu(self) -> bool:
        return self.startmenu_check.isChecked()

    def wants_autostart(self) -> bool:
        return self.autostart_check.isChecked()


def _center_on_screen(dialog: QDialog, parent=None) -> None:
    """把对话框摆到父窗口（或主屏）中央，并夹取到可视区内。"""
    screen = None
    if parent is not None and parent.isVisible():
        screen = parent.screen()
    if screen is None:
        screen = QApplication.primaryScreen()
    if screen is None:
        return
    geo = screen.availableGeometry()
    width = min(dialog.width(), geo.width())
    height = min(dialog.height(), geo.height())
    dialog.resize(width, height)

    if parent is not None and parent.isVisible():
        anchor = parent.frameGeometry()
    else:
        anchor = geo
    x = anchor.center().x() - width // 2
    y = anchor.center().y() - height // 2
    x = max(geo.left(), min(x, geo.right() - width))
    y = max(geo.top(), min(y, geo.bottom() - height))
    dialog.move(x, y)


def build_menu(window: PetWindow) -> QMenu:
    """统一的菜单构建：右键菜单与托盘菜单共用。"""
    menu = QMenu(window)
    menu.addAction("立即刷新", window.refresh)
    menu.addAction("切换单位", window.toggle_unit)
    menu.addAction("切换品种（当前：" + METALS[window.metal]["name"] + "）", window.toggle_metal)
    menu.addSeparator()
    menu.addAction("摸摸头", window.pat)
    menu.addAction("撒娇一下", window.shy)
    menu.addAction("让鲸娘说句话", lambda: window.speak())
    menu.addAction("叫她大肥鱼🐟", window.tease_fatty)  # v0.7.1：傲娇彩蛋，连点会记仇
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

    # v0.5：走势图时间范围子菜单（1 小时 / 今日 / 全部）
    range_menu = menu.addMenu("走势图范围")
    for key, label in SPARK_RANGE_NAMES.items():
        act = range_menu.addAction(label)
        act.setCheckable(True)
        act.setChecked(window.cfg.get("spark_range", "1h") == key)
        act.triggered.connect(lambda _checked=False, k=key: window.set_spark_range(k))

    menu.addSeparator()
    menu.addAction("提醒历史…", window.open_alert_history)
    menu.addAction("设置…", window.open_settings)

    # v0.5.4：快捷方式入口。桌面/开始菜单都没有时，给个「一键创建」；
    # 已有则不再刷屏，留「打开所在文件夹」帮用户找到它。
    if shortcut.is_supported():
        if not shortcut.is_created("desktop"):
            menu.addAction("创建桌面快捷方式", window.create_shortcuts)
        else:
            menu.addAction("打开程序所在文件夹", window.open_install_folder)

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
    # 记住默认图标，供数字图标渲染失败时回退
    window._base_tray_icon = icon

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
