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
from gold_pet.ui import LINES, STATES, PetWindow  # noqa: E402

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
    svc = PriceService(tmp_path / "last.json")
    hist = HistoryStore(tmp_path / "history.json")
    w = PetWindow(cfg, svc, hist, auto_refresh=False)  # 测试不联网
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

    cfg = Config()
    cfg.set("target_low", 900.0)  # 现价 897.15 应触发
    w = _window(app, tmp_path, cfg=cfg)
    w._render(parse_snapshot(RAW), silent=True)  # silent 不触发，下面手动调用
    w._last_target_at = 0.0
    quote = w.snapshot.price_of("gold", "cny_g")
    w._maybe_target(quote)
    assert w._last_target_side == "low"

    # 冷却期内不重复提醒
    before = w._last_target_side
    w._maybe_target(quote)
    assert w._last_target_side == before


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


def test_long_press_without_move_triggers_shy(app, tmp_path):
    """按住不动满 600ms 仍应正常触发撒娇。"""
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
