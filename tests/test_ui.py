"""UI 逻辑冒烟测试（无头 Qt）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication  # noqa: E402
except Exception as exc:  # 无显示环境 / 缺系统库时跳过 UI 测试
    pytest.skip(f"PySide6 不可用：{exc}", allow_module_level=True)

from gold_pet.config import Config  # noqa: E402
from gold_pet.price import HistoryStore, PriceService, parse_snapshot  # noqa: E402
from gold_pet.ui import (  # noqa: E402
    FATTY_LINES,
    LINES,
    STATES,
    PetWindow,
    pick_fatty_line,
)

RAW = {
    "gds_AUTD": "897.15,0,896.03,897.25,902.00,889.24,15:29:54,900.37,897.00,45270,2,7,2026-09-29,黄金延期".split(
        ","
    ),
    "hf_XAU": "4138.36,4114.93,4138.36,4138.71,4148.85,4113.30,16:37:00,4114.93,4117.01,0,0,0,2026-09-29,伦敦金".split(
        ","
    ),
    "fx_susdcny": "16:37:00,6.7038,6.7042,6.7118,61,6.7094,6.7094,6.7033,6.7042,x,-0.11,0,0,y,0,0,,2026-09-29".split(
        ","
    ),
}


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


def _window(app, tmp_path, cfg=None):
    cfg = cfg or Config()
    # R3 测试基建：测试窗口一律关闭 AI 控制服务。否则每个窗口都启动一个
    # ControlServer QThread（还会向真实 %APPDATA%\GoldPet\control.json 写发现
    # 文件），用例结束窗口被 GC 时 QThread 仍在 running，进程退出触发 Qt qFatal
    # （0xC0000409：pytest 汇总与退出码一并丢失）。
    cfg.set("control_enabled", False)
    svc = PriceService(tmp_path / "last.json")
    hist = HistoryStore(tmp_path / "history.json")
    # 提醒历史必须落到 tmp_path：否则会写进真实用户目录（%APPDATA%\GoldPet），
    # 既污染用户数据又让跨用例历史累积、断言失真。
    w = PetWindow(
        cfg,
        svc,
        hist,
        auto_refresh=False,  # 测试不联网
        notifier_history_path=tmp_path / "alert_history.json",
    )
    w.show()
    return w


def test_window_renders_price(app, tmp_path):
    w = _window(app, tmp_path)
    w._render(parse_snapshot(RAW), silent=True)
    text = w.bubble.text()
    assert "897.15" in text
    assert "元/克" in text
    assert "902.00" in text and "889.24" in text  # 最高 / 最低
    assert w.state == "down"  # 897.15 < 昨收 900.37


def test_toggle_unit_and_metal(app, tmp_path, monkeypatch):
    w = _window(app, tmp_path)
    monkeypatch.setattr(w, "refresh", lambda: None)  # 切换品种会触发联网刷新，测试中屏蔽
    w._render(parse_snapshot(RAW), silent=True)
    w.toggle_unit()
    assert w.unit == "usd_oz"
    assert "4138.36" in w.bubble.text()
    w.toggle_metal()
    assert w.metal in ("silver", "platinum", "gold")


def test_pat_and_shy_restore_price(app, tmp_path):
    w = _window(app, tmp_path)
    w._render(parse_snapshot(RAW), silent=True)
    w.pat()
    assert w.state == "pat"
    w._end_interact()
    assert w.state in STATES
    assert "897.15" in w.bubble.text()
    w.shy()
    assert w.state == "shy"
    w._end_interact()


def test_speak_and_restore(app, tmp_path):
    w = _window(app, tmp_path)
    w._render(parse_snapshot(RAW), silent=True)
    w.speak("up")
    assert any(line in w.bubble.text() for line in LINES["up"])
    w._restore_price()
    assert "897.15" in w.bubble.text()


def test_offline_state(app, tmp_path):
    w = _window(app, tmp_path)
    w._render(parse_snapshot({}), silent=True)  # 空数据 => 离线
    assert w.state == "offline"


def test_target_alert_triggers(app, tmp_path):
    """到价提醒已迁移到 Notifier（v0.5），此处验证界面侧接线正确。"""
    cfg = Config()
    cfg.set("target_low", 900.0)  # 现价 897.15 应触发
    w = _window(app, tmp_path, cfg=cfg)
    snap = parse_snapshot(RAW)
    w._render(snap, silent=True)

    quote = snap.price_of("gold", "cny_g")
    alerts = w.notifier.evaluate(quote, "黄金", base_quote=quote)
    kinds = {a.kind for a in alerts}
    assert "target" in kinds
    target = next(a for a in alerts if a.kind == "target")
    assert target.side == "low"

    # 冷却期内同方向不重复
    again = w.notifier.evaluate(quote, "黄金", base_quote=quote)
    assert not any(a.kind == "target" for a in again)


def test_dispatch_alerts_writes_history(app, tmp_path):
    """_dispatch_alerts 应把提醒写入历史并高亮气泡。"""
    from gold_pet.notifier import Alert

    w = _window(app, tmp_path)
    alert = Alert(kind="pct", side="up", title="t", body="黄金价上涨 1.00%")
    w._dispatch_alerts([alert])
    assert len(w.notifier.history()) == 1
    assert w.notifier.history()[0].body == "黄金价上涨 1.00%"


def test_quiet_hours_silences_notification(app, tmp_path):
    """勿扰时段内应跳过系统通知，但仍记录历史（气泡台词照常）。"""
    from gold_pet.notifier import Alert

    cfg = Config()
    cfg.set("dnd_enabled", True)
    cfg.set("dnd_start", "00:00")
    cfg.set("dnd_end", "23:59")  # 全天勿扰
    w = _window(app, tmp_path, cfg=cfg)

    sent: list = []

    class _FakeTray:
        def showMessage(self, *args):  # noqa: N802
            sent.append(args)

    w.attach_tray(_FakeTray())
    w._dispatch_alerts([Alert(kind="pct", side="up", title="t", body="b")])
    assert sent == []  # 未发通知
    assert len(w.notifier.history()) == 1  # 但历史已记录


def test_spark_range_switch(app, tmp_path):
    """走势图范围切换应写入配置并重绘，不影响价格显示。"""
    cfg = Config()
    w = _window(app, tmp_path, cfg=cfg)
    w._render(parse_snapshot(RAW), silent=True)

    w.set_spark_range("all")
    assert cfg.get("spark_range") == "all"
    assert "897.15" in w.bubble.text()

    w.set_spark_range("不合法范围")  # 非法值应被忽略
    assert cfg.get("spark_range") == "all"


def test_alert_history_dialog_construct(app, tmp_path):
    from gold_pet.notifier import Alert
    from gold_pet.ui import AlertHistoryDialog

    w = _window(app, tmp_path)
    w.notifier.record([Alert(kind="pct", side="up", title="t", body="黄金上涨")])
    dlg = AlertHistoryDialog(w.notifier, None)
    assert dlg.list.count() == 1
    assert "黄金上涨" in dlg.list.item(0).text()


def test_notifier_history_path_is_injectable(app, tmp_path):
    """回归：PetWindow 的提醒历史落盘路径必须可注入。

    此前 Notifier 固定取 alert_history_path()（真实 %APPDATA% 目录），
    导致测试写入用户真实数据，且跨用例历史累积、断言失真。
    """
    from gold_pet.paths import alert_history_path

    w = _window(app, tmp_path)
    assert w.notifier.history_path == tmp_path / "alert_history.json"
    assert w.notifier.history_path != alert_history_path()


def test_window_does_not_write_real_alert_history(app, tmp_path, monkeypatch):
    """回归：构造窗口并记录提醒后，真实用户目录不得被写入。"""
    import gold_pet.ui as ui_mod
    from gold_pet.notifier import Alert

    touched: list = []
    monkeypatch.setattr(ui_mod, "alert_history_path", lambda: touched.append(1))

    w = _window(app, tmp_path)
    w.notifier.record([Alert(kind="pct", side="up", title="t", body="x")])
    assert touched == []  # 显式传入路径时不应回落到真实路径


def test_settings_dialog_has_new_controls(app, tmp_path):
    """v0.5 设置面板应包含提醒开关、勿扰与走势范围。"""
    from gold_pet.ui import SettingsDialog

    dlg = SettingsDialog(Config(), None)
    for attr in (
        "pct_enabled_check",
        "target_enabled_check",
        "extreme_enabled_check",
        "dnd_check",
        "dnd_start_spin",
        "dnd_end_spin",
        "spark_range_combo",
        "tray_price_check",
    ):
        assert hasattr(dlg, attr), attr


def test_settings_dialog_has_bounded_size(app, tmp_path):
    """回归：设置面板曾被 QFormLayout 撑成满屏（实测 1920x1152）。

    现限制宽度并放进滚动区，尺寸应保持紧凑。
    """
    from gold_pet.ui import SettingsDialog

    dlg = SettingsDialog(Config(), None)
    dlg.show()
    app.processEvents()
    assert dlg.width() <= 560, f"设置面板过宽：{dlg.width()}"
    assert dlg.height() <= 900, f"设置面板过高：{dlg.height()}"

    screen = app.primaryScreen().availableGeometry()
    assert dlg.width() < screen.width() * 0.6, "设置面板不应接近满屏宽度"


def test_settings_dialog_is_scrollable(app, tmp_path):
    """设置项变多后必须有滚动区，否则小屏放不下底部按钮。"""
    from PySide6.QtWidgets import QScrollArea

    from gold_pet.ui import SettingsDialog

    dlg = SettingsDialog(Config(), None)
    scroll = dlg.findChild(QScrollArea)
    assert scroll is not None, "设置面板缺少滚动区"
    assert scroll.widgetResizable() is True


def test_settings_dialog_stays_on_screen(app, tmp_path):
    """回归：对话框应夹取在屏幕可视区内，标题栏与按钮都要点得到。"""
    from gold_pet.ui import SettingsDialog

    cfg = Config()
    dlg = SettingsDialog(cfg, None)
    dlg.show()
    app.processEvents()
    screen = app.primaryScreen().availableGeometry()
    geo = dlg.frameGeometry()
    assert screen.contains(geo.topLeft()), f"标题栏在屏幕外：{geo.topLeft()}"
    assert geo.right() <= screen.right() + 1
    assert geo.bottom() <= screen.bottom() + 1


def test_pet_window_snaps_inside_screen_with_margin(app, tmp_path):
    """回归：pos 为空时窗口应贴右下角但留边距，不得顶到屏幕边缘。

    此前 _move_to_default() 在布局定型前读取 width()/height()，
    导致算错边距（实测右边距仅 17px）。
    """
    cfg = Config()
    assert cfg.get("pos") is None
    w = _window(app, tmp_path, cfg=cfg)
    for _ in range(10):
        app.processEvents()

    screen = app.primaryScreen().availableGeometry()
    geo = w.frameGeometry()
    assert screen.contains(geo.topLeft()), f"窗口左上角在屏幕外：{geo.topLeft()}"
    assert geo.right() <= screen.right(), "窗口超出右边界"
    assert geo.left() >= screen.left(), "窗口超出左边界"
    # 应留出一定边距，而不是紧贴边缘
    assert screen.right() - geo.right() >= 4, "右边距过小，窗口贴边了"


def test_tray_icon_renders_number(app, tmp_path):
    """托盘数字图标应按价格渲染；位数过长时回退。"""
    from gold_pet.tray_icon import render_price_icon, tray_text

    assert tray_text(901.75) == "901"
    assert tray_text(14.801) == "14"
    assert tray_text(12345.0) == ""  # 5 位放不下
    assert tray_text(0) == ""

    icon = render_price_icon(901.75, change=1.0)
    assert icon is not None and not icon.isNull()
    assert render_price_icon(12345.0, change=1.0) is None


def test_market_status_tag_in_bubble(app, tmp_path, monkeypatch):
    import gold_pet.ui as ui_mod

    monkeypatch.setattr(ui_mod, "is_market_open", lambda *a, **k: False)
    w = _window(app, tmp_path, cfg=Config())
    w._render(parse_snapshot(RAW), silent=True)
    assert "休市" in w.bubble.text()


def test_failure_backoff_schedules_retry(app, tmp_path):
    w = _window(app, tmp_path)
    w._on_snapshot(parse_snapshot({}))
    assert w._fail_streak == 1
    assert w._retry_timer.isActive()
    w._retry_timer.stop()


# ------------------------------------------------------- v0.4.1 新增 / 回归


def test_welcome_exists_and_speaks(app, tmp_path):
    """P0 回归：main.py 会调用 window.welcome()，此前该方法不存在导致首次启动崩溃。"""
    w = _window(app, tmp_path)
    assert callable(getattr(w, "welcome", None))
    w.welcome()
    assert any(line in w.bubble.text() for line in LINES["welcome"])


def test_welcome_without_tray_does_not_crash(app, tmp_path):
    """无托盘环境（tray_icon() 为 None）下引导不应抛异常。"""
    w = _window(app, tmp_path)
    assert w.tray_icon() is None
    w.welcome()


def test_open_about_exists(app, tmp_path):
    """P0 回归：菜单里的「关于…」此前指向不存在的方法。"""
    w = _window(app, tmp_path)
    assert callable(getattr(w, "open_about", None))


def test_about_and_settings_dialog_construct(app, tmp_path):
    from gold_pet.ui import AboutDialog, SettingsDialog

    cfg = Config()
    assert AboutDialog(None) is not None
    assert SettingsDialog(cfg, None) is not None


def test_build_menu_contains_about_and_settings(app, tmp_path):
    """菜单统一后，右键与托盘菜单应包含关于与设置。"""
    from gold_pet.ui import build_menu

    w = _window(app, tmp_path)
    labels = [a.text() for a in build_menu(w).actions() if a.text()]
    assert any("关于" in t for t in labels)
    assert any("设置" in t for t in labels)
    assert any("刷新" in t for t in labels)
    # 无托盘时不应出现「隐藏到托盘」，否则点了找不回来
    assert not any("隐藏到托盘" in t for t in labels)


def test_menu_hides_tray_entry_when_no_tray(app, tmp_path):
    from gold_pet.ui import build_menu

    w = _window(app, tmp_path)

    class _FakeTray:
        pass

    w.attach_tray(_FakeTray())
    labels = [a.text() for a in build_menu(w).actions() if a.text()]
    assert any("隐藏到托盘" in t for t in labels)


def test_drag_does_not_trigger_shy(app, tmp_path):
    """回归：按住拖动超过 600ms 会被误判为长按撒娇。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    w = _window(app, tmp_path)

    def mouse(kind, gx, gy):
        return QMouseEvent(
            kind, QPointF(10, 10), QPointF(gx, gy), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
        )

    w.mousePressEvent(mouse(QEvent.MouseButtonPress, 100, 100))
    w.mouseMoveEvent(mouse(QEvent.MouseMove, 180, 160))

    assert w._moved is True
    assert w._press_timer.isActive() is False  # 拖动后长按计时必须被取消
    w._on_long_press()
    assert w._long_pressed is False
    assert w.state != "shy"


def _drag_to(w, gx, gy):
    """模拟一次「按下 → 拖到 (gx, gy)」的鼠标操作。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    def mouse(kind, x, y):
        return QMouseEvent(
            kind, QPointF(10, 10), QPointF(x, y), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
        )

    w.mousePressEvent(mouse(QEvent.MouseButtonPress, 100, 100))
    w.mouseMoveEvent(mouse(QEvent.MouseMove, gx, gy))


def test_drag_to_right_edge_keeps_window_inside_screen(app, tmp_path):
    """回归：拖动到屏幕右边缘会「卡半边」。

    根因是 mouseMoveEvent 直接 move(鼠标位置 - 拖拽偏移)，没有任何夹取。
    窗口实测 176×239（气泡在鲸娘上方，比鲸娘本体高 79px），
    拖到最右时右边界会超出屏幕 155px，气泡文字与鲸娘右半边被裁掉。
    """
    w = _window(app, tmp_path)
    for _ in range(5):
        app.processEvents()
    screen = w.screen().availableGeometry()

    _drag_to(w, screen.right() + 500, w.y() + 10)
    for _ in range(3):
        app.processEvents()

    geo = w.frameGeometry()
    assert geo.right() <= screen.right(), f"窗口右边界 {geo.right()} 超出屏幕 {screen.right()}"
    assert geo.left() >= screen.left(), "窗口左边界在屏幕外"


def test_drag_to_bottom_edge_keeps_window_inside_screen(app, tmp_path):
    """回归：拖动到屏幕下边缘应被夹住（此前下边界可超出 218px）。"""
    w = _window(app, tmp_path)
    for _ in range(5):
        app.processEvents()
    screen = w.screen().availableGeometry()

    _drag_to(w, w.x() + 10, screen.bottom() + 500)
    for _ in range(3):
        app.processEvents()

    geo = w.frameGeometry()
    assert geo.bottom() <= screen.bottom(), f"窗口下边界 {geo.bottom()} 超出屏幕 {screen.bottom()}"
    assert geo.top() >= screen.top(), "窗口上边界在屏幕外"


def test_drag_to_top_left_corner_is_clamped(app, tmp_path):
    """反方向同样要夹取：拖到左上角不能把窗口推到屏幕外。"""
    w = _window(app, tmp_path)
    for _ in range(5):
        app.processEvents()
    screen = w.screen().availableGeometry()

    _drag_to(w, screen.left() - 800, screen.top() - 800)
    for _ in range(3):
        app.processEvents()

    geo = w.frameGeometry()
    assert geo.left() >= screen.left(), "窗口左边界越出屏幕"
    assert geo.top() >= screen.top(), "窗口上边界越出屏幕"


def test_clamp_allows_inner_positions_untouched(app, tmp_path):
    """夹取只处理越界，屏幕内的合法位置必须原样保留（不能把窗口吸到角落）。"""
    from PySide6.QtCore import QPoint

    w = _window(app, tmp_path)
    for _ in range(5):
        app.processEvents()
    screen = w.screen().availableGeometry()
    width, height = w._window_size()

    inner_x = screen.left() + (screen.width() - width) // 2
    inner_y = screen.top() + (screen.height() - height) // 2
    assert w._clamp_to_screen(inner_x, inner_y) == QPoint(inner_x, inner_y)


def test_clamp_keeps_whole_window_visible(app, tmp_path):
    """夹取后整个窗口（含气泡与鲸娘）都应落在可用区内。"""
    w = _window(app, tmp_path)
    for _ in range(5):
        app.processEvents()
    screen = w.screen().availableGeometry()

    far = w._clamp_to_screen(screen.right() + 9999, screen.bottom() + 9999)
    w.move(far)
    for _ in range(3):
        app.processEvents()

    geo = w.frameGeometry()
    assert screen.contains(geo), f"夹取后窗口仍不在可用区内：{geo} ⊄ {screen}"


def test_clamp_is_noop_without_screen(app, tmp_path, monkeypatch):
    """极端环境（无屏幕信息）不得抛异常，应原样返回坐标。"""
    from PySide6.QtCore import QPoint

    import gold_pet.ui as ui_mod

    w = _window(app, tmp_path)
    monkeypatch.setattr(ui_mod.QApplication, "screens", staticmethod(lambda: []))
    monkeypatch.setattr(w, "screen", lambda: None)
    monkeypatch.setattr(ui_mod.QApplication, "primaryScreen", staticmethod(lambda: None))

    assert w._clamp_to_screen(123, 456) == QPoint(123, 456)


def test_clamp_falls_back_when_screen_smaller_than_window(app, tmp_path, monkeypatch):
    """屏幕比窗口还小时退化为「左上角入屏」，不得算出屏幕外的负坐标。"""
    from PySide6.QtCore import QPoint, QRect

    class _TinyScreen:
        def availableGeometry(self):
            return QRect(0, 0, 50, 40)

    w = _window(app, tmp_path)
    monkeypatch.setattr(w, "screen", lambda: _TinyScreen())

    point = w._clamp_to_screen(9999, 9999)
    assert point.x() <= 50 and point.y() <= 40
    assert point.x() >= 0 and point.y() >= 0
    assert isinstance(point, QPoint)


def test_clamp_picks_screen_containing_target(app, tmp_path):
    """多屏：目标点在哪块屏幕，就按那块屏幕夹取（跨屏拖动才顺滑）。"""
    from PySide6.QtCore import QRect

    class _Screen:
        def __init__(self, rect):
            self._rect = rect

        def availableGeometry(self):
            return self._rect

    left = _Screen(QRect(0, 0, 1920, 1080))
    right = _Screen(QRect(1920, 0, 1920, 1080))
    w = _window(app, tmp_path)
    w.resize(176, 239)

    # 目标点在右屏内 → 应按右屏边界夹取，左边界允许 > 0（不被拽回左屏）
    point = w._clamp_to_screen(3000, 100, screens_override=[left, right])
    assert point.x() >= 1920, f"跨屏拖动被拽回左屏：x={point.x()}"


def test_first_run_dialog_construct_and_defaults(app, tmp_path):
    """首启引导对话框可构造，默认勾选桌面+开始菜单，自启默认不勾。

    默认值即产品决策：桌面与开始菜单是「找得到鲸娘」的关键，默认给；
    开机自启属于用户偏好，不替用户决定。
    """
    from gold_pet.ui import FirstRunDialog

    dlg = FirstRunDialog(None, has_tray=True)
    assert dlg.windowTitle() == "欢迎使用 黄金桌宠"
    assert dlg.isModal() is True
    assert dlg.wants_desktop() is True
    assert dlg.wants_startmenu() is True
    assert dlg.wants_autostart() is False
    assert 380 <= dlg.width() <= 560, f"对话框宽度异常：{dlg.width()}"
    dlg.deleteLater()


def test_first_run_dialog_bounded_size(app, tmp_path):
    """对话框不得撑满屏幕（v0.5.1 设置面板踩过这个坑）。"""
    from gold_pet.ui import FirstRunDialog

    dlg = FirstRunDialog(None, has_tray=True)
    screen = app.primaryScreen().availableGeometry()
    assert dlg.width() <= min(560, screen.width())
    assert dlg.height() <= screen.height()
    dlg.deleteLater()


def test_first_run_dialog_without_tray_mentions_exit(app, tmp_path):
    """无托盘环境必须在文案里说清「关窗即退出」，不能让用户以为还在后台跑。"""
    from gold_pet.ui import FirstRunDialog

    dlg = FirstRunDialog(None, has_tray=False)
    texts = " ".join(
        w.text() for w in dlg.findChildren(type(dlg.desktop_check).__mro__[0]) if hasattr(w, "text")
    )
    # 直接检查所有 QLabel 的文案
    from PySide6.QtWidgets import QLabel

    joined = " ".join(lb.text() for lb in dlg.findChildren(QLabel))
    assert "退出" in joined, "无托盘时未提示关窗即退出"
    assert "托盘" not in joined, "无托盘时不应提托盘"
    assert texts is not None
    dlg.deleteLater()


def test_first_run_dialog_with_tray_mentions_tray(app, tmp_path):
    """有托盘时应告诉用户鲸娘藏在托盘 ^ 里 —— 这是小白找不到她的主要原因。"""
    from PySide6.QtWidgets import QLabel

    from gold_pet.ui import FirstRunDialog

    dlg = FirstRunDialog(None, has_tray=True)
    joined = " ".join(lb.text() for lb in dlg.findChildren(QLabel))
    assert "托盘" in joined, "有托盘时未说明鲸娘在托盘里"
    dlg.deleteLater()


def test_menu_shows_create_shortcut_when_missing(app, tmp_path, monkeypatch):
    """桌面没有快捷方式时，菜单应出现「创建桌面快捷方式」。"""
    import gold_pet.ui as ui_mod
    from gold_pet.shortcut import is_supported

    if not is_supported():
        pytest.skip("当前平台不支持快捷方式")

    monkeypatch.setattr(ui_mod.shortcut, "is_created", lambda where: False)
    w = _window(app, tmp_path)
    menu = ui_mod.build_menu(w)
    labels = [a.text() for a in menu.actions()]
    assert "创建桌面快捷方式" in labels, f"菜单缺少创建入口：{labels}"


def test_menu_shows_open_folder_when_created(app, tmp_path, monkeypatch):
    """已有快捷方式时不再重复提示创建，改为「打开程序所在文件夹」。"""
    import gold_pet.ui as ui_mod
    from gold_pet.shortcut import is_supported

    if not is_supported():
        pytest.skip("当前平台不支持快捷方式")

    monkeypatch.setattr(ui_mod.shortcut, "is_created", lambda where: True)
    w = _window(app, tmp_path)
    menu = ui_mod.build_menu(w)
    labels = [a.text() for a in menu.actions()]
    assert "打开程序所在文件夹" in labels, f"菜单缺少打开文件夹入口：{labels}"
    assert "创建桌面快捷方式" not in labels


def test_menu_hides_shortcut_when_unsupported(app, tmp_path, monkeypatch):
    """不支持快捷方式的平台不应出现相关菜单项，避免点了没反应。"""
    import gold_pet.ui as ui_mod

    monkeypatch.setattr(ui_mod.shortcut, "is_supported", lambda: False)
    w = _window(app, tmp_path)
    menu = ui_mod.build_menu(w)
    labels = [a.text() for a in menu.actions()]
    assert "创建桌面快捷方式" not in labels
    assert "打开程序所在文件夹" not in labels


def test_create_shortcuts_reports_result_in_bubble(app, tmp_path, monkeypatch):
    """创建结果要通过气泡反馈给用户，不能静默成功/失败。"""
    import gold_pet.ui as ui_mod

    created = {}
    monkeypatch.setattr(
        ui_mod.shortcut, "create_many", lambda wheres, icon=None: {"desktop": True,
                                                                   "startmenu": True}
    )
    monkeypatch.setattr(ui_mod.shortcut, "is_supported", lambda: True)
    w = _window(app, tmp_path)
    w.create_shortcuts()
    text = w.bubble.text()
    created["text"] = text
    assert "桌面" in text or "成功" in text, f"未反馈创建结果：{text!r}"


def test_create_shortcuts_failure_gives_friendly_message(app, tmp_path, monkeypatch):
    """创建失败要有友好提示，而不是抛异常或静默。"""
    import gold_pet.ui as ui_mod

    monkeypatch.setattr(
        ui_mod.shortcut, "create_many", lambda wheres, icon=None: {"desktop": False,
                                                                   "startmenu": False}
    )
    monkeypatch.setattr(ui_mod.shortcut, "is_supported", lambda: True)
    w = _window(app, tmp_path)
    w.create_shortcuts()
    assert "失败" in w.bubble.text()


def test_bubble_say_restores_price(app, tmp_path):
    """bubble_say 的临时消息到点后必须恢复行情，不能一直挂着系统提示。"""
    w = _window(app, tmp_path)
    snap = parse_snapshot(RAW)

    w._render(snap, silent=True)
    before = w.bubble.text()
    w.bubble_say("测试消息", ms=50)
    assert "测试消息" in w.bubble.text()

    w._restore_bubble()
    assert w.bubble.text() == before, "恢复后气泡未回到行情内容"


def test_settings_dialog_has_shortcut_controls(app, tmp_path, monkeypatch):
    """设置面板应能管理快捷方式（创建后可随时取消）。"""
    import gold_pet.ui as ui_mod

    monkeypatch.setattr(ui_mod.shortcut, "is_supported", lambda: True)
    monkeypatch.setattr(ui_mod.shortcut, "is_created", lambda where: False)
    dlg = ui_mod.SettingsDialog(Config())
    assert hasattr(dlg, "desktop_link_check")
    assert hasattr(dlg, "startmenu_link_check")
    assert dlg.desktop_link_check.isChecked() is False
    dlg.deleteLater()


def test_settings_dialog_saves_shortcut_state(app, tmp_path, monkeypatch):
    """设置面板保存时，勾选要调用创建、取消勾选要调用删除。"""
    import gold_pet.ui as ui_mod

    calls = {"create": [], "remove": []}
    monkeypatch.setattr(ui_mod.shortcut, "is_supported", lambda: True)
    monkeypatch.setattr(ui_mod.shortcut, "is_created", lambda where: False)
    monkeypatch.setattr(
        ui_mod.shortcut, "create", lambda where, icon=None: calls["create"].append(where) or True
    )
    monkeypatch.setattr(
        ui_mod.shortcut, "remove", lambda where: calls["remove"].append(where) or True
    )

    cfg = Config()
    dlg = ui_mod.SettingsDialog(cfg)
    dlg.desktop_link_check.setChecked(True)
    dlg.startmenu_link_check.setChecked(False)
    dlg.accept()
    assert calls["create"] == ["desktop"], calls
    assert calls["remove"] == ["startmenu"], calls


def test_long_press_without_move_triggers_shy(app, tmp_path):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    w = _window(app, tmp_path)
    w.mousePressEvent(
        QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(10, 10),
            QPointF(100, 100),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    w._on_long_press()
    assert w._long_pressed is True
    assert w.state == "shy"


def test_bubble_hidden_skips_render(app, tmp_path):
    """关闭气泡时不应再往 QLabel 里写 HTML（无谓开销）。"""
    cfg = Config()
    cfg.set("show_bubble", False)
    w = _window(app, tmp_path, cfg=cfg)
    before = w.bubble.text()
    w._render(parse_snapshot(RAW), silent=True)
    assert w.bubble.text() == before  # 未被改写
    assert w.bubble.isVisible() is False


def test_offline_cache_shows_staleness(app, tmp_path):
    """离线且缓存较旧时，气泡应提示数据陈旧，避免被当成现价。"""
    import time as _time

    from gold_pet.bubble import render_cache_html

    w = _window(app, tmp_path)
    snap = parse_snapshot(RAW)
    w.service.save_cache(snap)
    quote = snap.price_of("gold", "cny_g")

    fresh = render_cache_html("黄金", quote, updated_at=_time.time() - 60)
    assert "缓存于" in fresh
    assert "已过期" not in fresh

    stale = render_cache_html("黄金", quote, updated_at=_time.time() - 5 * 3600)
    assert "已过期 5 小时" in stale

    ancient = render_cache_html("黄金", quote, updated_at=_time.time() - 100 * 3600)
    assert "数据陈旧" in ancient

    assert "缓存于" not in render_cache_html("黄金", quote)


# ---------------------------------------------------------------- v0.7.1 大肥鱼彩蛋


def test_pick_fatty_line_tiers():
    """递进层次：1-2 次嘴硬反驳 / 3-4 次生气甩尾 / 5+ 次记仇别扭。"""
    for count in (1, 2):
        assert pick_fatty_line(count) in FATTY_LINES[1], f"count={count} 应落在反驳层"
    for count in (3, 4):
        assert pick_fatty_line(count) in FATTY_LINES[2], f"count={count} 应落在生气层"
    for count in (5, 9):
        assert pick_fatty_line(count) in FATTY_LINES[3], f"count={count} 应落在记仇层"


def test_fatty_lines_have_three_tiers_of_six():
    """三层各 6 条，台词不与其他层重复。"""
    assert set(FATTY_LINES) == {1, 2, 3}
    pools = [set(FATTY_LINES[t]) for t in (1, 2, 3)]
    for pool in pools:
        assert len(pool) >= 6, "每层至少 6 条"
    assert not (pools[0] & pools[1] or pools[1] & pools[2] or pools[0] & pools[2]), "层间台词不应重复"


def test_tease_fatty_progresses_and_pat_resets(app, tmp_path):
    """连叫计数递进；摸摸头哄好归零。"""
    w = _window(app, tmp_path)
    assert w.fatty_count == 0
    for _ in range(3):
        w.tease_fatty()
    assert w.fatty_count == 3
    w.pat()
    assert w.fatty_count == 0


def test_no_gold_girl_naming_regression():
    """防回潮：台词池与 ui.py 源码不得再出现「金娘」（v0.7.1 起统一称鲸娘）。"""
    for group, lines in {**LINES, **FATTY_LINES}.items():
        for line in lines:
            assert "金娘" not in line, f"台词组 {group} 出现旧称呼：{line}"
    src = Path(__file__).resolve().parent.parent / "gold_pet" / "ui.py"
    assert "金娘" not in src.read_text(encoding="utf-8"), "ui.py 仍存在旧称呼「金娘」"


# ---------------------------------------------------------------- v0.7.2 安静档静止


def test_activity_lines_exist():
    """三档切换台词组存在，各 ≥3 条；安静组必含「安静美少女」。"""
    for key in ("act_quiet", "act_normal", "act_lively"):
        assert key in LINES and len(LINES[key]) >= 3, f"{key} 台词缺失或太少"
    assert any("安静美少女" in line for line in LINES["act_quiet"]), "安静组缺「安静美少女」"


def test_anim_interval_zero_means_frozen(app, tmp_path):
    """quiet 系数 0 → 帧间隔 0（静止）；normal/lively 按系数换算。"""
    w = _window(app, tmp_path)
    w.cfg.set("activity_level", "quiet")
    assert w._anim_interval("idle") == 0
    w.cfg.set("activity_level", "lively")
    from gold_pet.config import ACTIVITY_LEVELS

    factor = ACTIVITY_LEVELS["lively"][1]
    assert w._anim_interval("idle") == round(STATES["idle"][1] * factor)


def test_quiet_level_freezes_animation(app, tmp_path):
    """安静档：帧循环停表 + 定格第 0 帧；切回标准后恢复跑动。"""
    w = _window(app, tmp_path)
    w.cfg.set("activity_level", "quiet")
    w._apply_activity()
    assert not w.anim_timer.isActive(), "安静档帧循环应停住"
    assert w.frame_idx == 0
    frames = w.frames.get("idle") or []
    assert frames and w.pet.pixmap().cacheKey() == frames[0].cacheKey()

    w.cfg.set("activity_level", "normal")
    w._apply_activity()
    assert w.anim_timer.isActive(), "标准档帧循环应恢复"
    assert w.anim_timer.interval() == STATES["idle"][1]


def test_apply_activity_speaks_level_line(app, tmp_path):
    """切档说话：切到安静说 act_quiet 组台词；档位没变则不说话。"""
    w = _window(app, tmp_path)
    before = w.bubble.text()
    w.cfg.set("activity_level", "quiet")
    w._apply_activity()
    assert any(line in w.bubble.text() for line in LINES["act_quiet"]), "切安静应说对应台词"
    assert w._activity_level == "quiet"

    w.bubble.setText(before)
    w._apply_activity()  # 档位没变：不应说话
    assert w.bubble.text() == before


# ---------------------------------------------------------------- v0.7.3 全局图标统一


def test_dialogs_have_whale_icon(app, tmp_path):
    """四个弹窗显式设置鲸鱼娘标题栏图标，不得回落 Qt 默认小鸟。"""
    from gold_pet.ui import (
        AboutDialog,
        AlertHistoryDialog,
        FirstRunDialog,
        SettingsDialog,
        _app_icon,
    )

    icon = _app_icon()
    assert not icon.isNull(), "assets/icon.png 应存在（鲸鱼娘）"
    w = _window(app, tmp_path)
    dialogs = [
        SettingsDialog(w.cfg, w),
        AboutDialog(w),
        AlertHistoryDialog(w.notifier, w),
        FirstRunDialog(w),
    ]
    for dlg in dialogs:
        assert not dlg.windowIcon().isNull(), f"{type(dlg).__name__} 缺窗口图标"
        same = dlg.windowIcon().pixmap(64, 64).toImage() == icon.pixmap(64, 64).toImage()
        assert same, f"{type(dlg).__name__} 图标不是统一鲸鱼娘"
        dlg.deleteLater()


def test_no_old_mascot_emoji_regression():
    """防回潮：不得再出现旧时代小鸡/王冠形象（v0.6.0 起为鲸鱼娘）。"""

    src = Path(__file__).resolve().parent.parent / "gold_pet" / "ui.py"
    text = src.read_text(encoding="utf-8")
    for bad in ("🐥", "🐤", "王冠"):
        assert bad not in text, f"ui.py 出现旧形象元素「{bad}」"
    for group, lines in LINES.items():
        for line in lines:
            assert "🐥" not in line and "王冠" not in line, f"台词组 {group} 有旧形象：{line}"
