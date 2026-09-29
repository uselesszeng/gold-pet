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
    "gds_AUTD": "897.15,0,896.03,897.25,902.00,889.24,15:29:54,900.37,897.00,45270,2,7,2026-09-29,黄金延期".split(","),
    "hf_XAU": "4138.36,4114.93,4138.36,4138.71,4148.85,4113.30,16:37:00,4114.93,4117.01,0,0,0,2026-09-29,伦敦金".split(","),
    "fx_susdcny": "16:37:00,6.7038,6.7042,6.7118,61,6.7094,6.7094,6.7033,6.7042,x,-0.11,0,0,y,0,0,,2026-09-29".split(","),
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


def test_failure_backoff_schedules_retry(app, tmp_path):
    w = _window(app, tmp_path)
    w._on_snapshot(parse_snapshot({}))
    assert w._fail_streak == 1
    assert w._retry_timer.isActive()
    w._retry_timer.stop()
