"""Monkey 测试 R3 目标 7：降级环境（assets 缺失/部分缺失、配置全缺、无网络、日志不可写）。

种子：20261027。
降级路径必须在「资源不完整」时保持主循环可用：

  - assets/pet 整目录缺失 → 窗口可构造、可渲染、可绘制、可说话（无逐帧动画）；
  - assets 只含部分状态帧 → 状态回落 idle，不崩；
  - settings.json 缺失/键全缺/随机残缺 → 全部回落 DEFAULTS 且 sanitize 后值域合法；
  - 断网（fetch_raw 抛 URLError）→ fetch 结构化失败、UI 显示缓存、CLI 仍可读缓存，
    run_once 返回 1 而不是崩；
  - 日志目录不可写 → setup_logging 降级为仅控制台（R3-M-04 回归，子进程验证）。

oracle：不崩；默认配置值域合法；离线链路退出码 ∈ {0,4} 且 --json 合法。
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import cli as cli_mod
from gold_pet import config as config_mod
from gold_pet import price as price_mod
from gold_pet.config import ACTIVITY_LEVELS, DEFAULTS, INTERVAL_CHOICES, Config
from gold_pet.price import HistoryStore, PriceService, Quote, Snapshot

SEED = 20261027
ITERATIONS = 500
PROJECT_ROOT = Path(__file__).resolve().parent.parent

NET_ERRORS = (
    urllib.error.URLError("name resolution failed"),
    TimeoutError("timed out"),
    ConnectionResetError(10054, "reset"),
    OSError(10051, "network unreachable"),
)


def _good_snapshot() -> Snapshot:
    q = Quote(
        code="gds_AUTD",
        name="黄金延期",
        price=897.15,
        prev_close=900.37,
        high=902.0,
        low=889.24,
        unit="元/克",
        currency="CNY",
    )
    return Snapshot(quotes={"gold|cny_g": q}, fx=6.7, updated_at=1790000000.0, ok=True, error="")


def _window(tmp_path, cfg=None, asset_root=None, monkeypatch=None):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from gold_pet import ui as ui_mod

    if asset_root is not None:
        monkeypatch.setattr(ui_mod, "asset_dir", lambda: asset_root)
    QApplication.instance() or QApplication([])
    cfg = cfg or Config()
    cfg.set("control_enabled", False)
    w = ui_mod.PetWindow(
        cfg,
        PriceService(tmp_path / "last.json"),
        HistoryStore(tmp_path / "history.json"),
        auto_refresh=False,
        notifier_history_path=tmp_path / "alert_history.json",
    )
    return w


# ------------------------------------------------------------------ assets
def test_r3_degraded_assets_fully_missing(tmp_path, monkeypatch):
    """assets/pet 整目录缺失：窗口构造/渲染/绘制/说话全部可用。"""
    w = _window(tmp_path, asset_root=tmp_path / "no_such_assets", monkeypatch=monkeypatch)
    try:
        assert w.frames == {}
        w._render(_good_snapshot(), silent=True)
        assert "897.15" in w.bubble.text()
        w.grab()  # 强制走 paintEvent
        w.speak("up")
        assert w.bubble.text()
        w._tick_frame()  # 无帧时 no-op
        w._set_state("up")
        w._set_state("offline")
    finally:
        w.deleteLater()


def test_r3_degraded_assets_partial(tmp_path, monkeypatch):
    """只提供 idle 一帧：所有状态回落 idle；帧循环/交互不崩。"""
    Image = pytest.importorskip("PIL.Image")
    root = tmp_path / "assets"
    (root / "pet").mkdir(parents=True)
    Image.new("RGBA", (160, 160), (0, 0, 0, 0)).save(root / "pet" / "idle_0.png")
    w = _window(tmp_path, asset_root=root, monkeypatch=monkeypatch)
    try:
        assert set(w.frames) == {"idle"}
        for state in ("up", "down", "pat", "shy", "loading", "offline"):
            w._set_state(state)
            assert w.state == "idle"  # 缺失状态回落
        w._render(_good_snapshot(), silent=True)
        w.grab()
        w.pat()
        w._end_interact()
    finally:
        w.deleteLater()


# ------------------------------------------------------------------ config
def test_r3_degraded_config_missing_file(tmp_path, monkeypatch):
    """settings.json 不存在：全部取 DEFAULTS，值域合法。"""
    monkeypatch.setattr(config_mod, "settings_path", lambda: tmp_path / "settings.json")
    cfg = Config.load()
    for key in DEFAULTS:
        assert cfg.get(key) is not None or DEFAULTS[key] is None
    assert cfg.get("metal") in ("gold", "silver", "platinum")
    assert cfg.get("interval_sec") in INTERVAL_CHOICES
    assert cfg.get("activity_level") in ACTIVITY_LEVELS
    assert 0.3 <= cfg.get("opacity") <= 1.0
    cfg.save()
    cfg2 = Config.load()
    assert cfg2.as_dict() == cfg.as_dict()


def test_r3_degraded_config_random_key_subsets():
    """500 次随机残缺配置（键全缺/随机子集/None/脏值）：sanitize 后值域恒合法。"""
    rnd = random.Random(SEED)
    keys = list(DEFAULTS)
    dirty = [
        None,
        [],
        {},
        "",
        "NaN",
        float("nan"),
        float("inf"),
        True,
        -(10**9),
        10**400,
        "abc",
        [1],
        {"a": 1},
    ]
    for _ in range(ITERATIONS):
        subset = rnd.sample(keys, rnd.randint(0, len(keys)))
        data = {}
        for k in subset:
            data[k] = rnd.choice(dirty) if rnd.random() < 0.7 else DEFAULTS[k]
        cfg = Config(data)
        cfg.sanitize()
        d = cfg.as_dict()
        # 每个默认键都必须存在且落在合法域
        assert set(DEFAULTS) <= set(d)
        assert d["metal"] in ("gold", "silver", "platinum")
        assert d["unit"] in ("cny_g", "usd_oz")
        assert d["interval_sec"] in INTERVAL_CHOICES
        assert 0.0 <= d["alert_pct"] <= 20.0
        assert 60 <= d["alert_cooldown_sec"] <= 7200
        assert 0.0 <= d["alert_extreme_step_pct"] <= 5.0
        assert 0.3 <= d["opacity"] <= 1.0
        assert d["spark_range"] in ("1h", "today", "all")
        assert d["activity_level"] in ACTIVITY_LEVELS
        assert d["pos"] is None or (isinstance(d["pos"], list) and len(d["pos"]) == 2)
        assert isinstance(d["target_high"], float) and d["target_high"] >= 0.0
        assert isinstance(d["target_low"], float) and d["target_low"] >= 0.0
        for bk in (
            "always_on_top",
            "show_bubble",
            "show_spark",
            "autostart",
            "alert_pct_enabled",
            "alert_target_enabled",
            "alert_extreme_enabled",
            "dnd_enabled",
            "tray_show_price",
            "control_enabled",
            "care_drink_enabled",
            "care_posture_enabled",
            "care_eye_enabled",
        ):
            assert isinstance(d[bk], bool)
        for ck in ("care_drink_interval_min", "care_posture_interval_min", "care_eye_interval_min"):
            assert d[ck] in (30, 45, 60, 90, 120)


# ------------------------------------------------------------------ 无网络
def test_r3_degraded_no_network_pipeline(tmp_path, monkeypatch):
    """断网：fetch 结构化失败 → UI 显示缓存；CLI price 仍可读缓存；run_once 返回 1。"""
    cache = tmp_path / "last_price.json"
    svc = PriceService(cache)
    svc.save_cache(_good_snapshot())

    for exc in NET_ERRORS:

        def boom(timeout=8.0, exc=exc):
            raise exc

        monkeypatch.setattr(price_mod, "fetch_raw", boom)
        snap = svc.fetch()
        assert not snap.ok and snap.error
        assert svc.load_cache() is not None

    # UI 离线路径：失败快照 → 回显缓存
    w = _window(tmp_path, monkeypatch=monkeypatch)
    try:
        monkeypatch.setattr(w.service, "load_cache", lambda: _good_snapshot())
        w._render(Snapshot(ok=False, error="断网"), silent=True)
        assert w.state == "offline"
        assert "897.15" in w.bubble.text()
        assert "缓存" in w.bubble.text()
    finally:
        w.deleteLater()

    # CLI：缓存 ok=True 时离线仍可读；ok=False 时 exit 4
    monkeypatch.setattr(cli_mod, "cache_path", lambda: cache)
    monkeypatch.setattr(cli_mod, "history_path", lambda: tmp_path / "history.json")
    monkeypatch.setattr(cli_mod, "alert_history_path", lambda: tmp_path / "alert_history.json")
    monkeypatch.setattr(config_mod, "settings_path", lambda: tmp_path / "settings.json")
    code = cli_mod.main(["price", "--json"])
    assert code == 0

    bad = Snapshot(ok=False, error="上游不可达")
    PriceService(cache).save_cache(bad)
    code2 = cli_mod.main(["price", "--json"])
    assert code2 == 4

    # run_once：无网络返回 1（不裸 traceback）
    from gold_pet import main as main_mod

    monkeypatch.setattr(main_mod, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(
        price_mod,
        "fetch_raw",
        lambda timeout=8.0: (_ for _ in ()).throw(urllib.error.URLError("down")),
    )
    args = main_mod.parse_args(["--once"])
    assert main_mod.run_once(args, Config()) == 1


def test_r3_degraded_history_no_network_no_crash(tmp_path, monkeypatch):
    """无网络 + 空历史：UI 刷新失败后走势图回退分支不炸（200 次）。"""
    w = _window(tmp_path, monkeypatch=monkeypatch)
    try:
        w.history.append("gold", "cny_g", 900.0)
        for i in range(200):
            w._render(Snapshot(ok=False, error=f"断网{i}"), silent=True)
        assert w.state == "offline"
    finally:
        w.deleteLater()


# ------------------------------------------------------------------ 日志
def test_r3_degraded_logging_unwritable(tmp_path):
    """日志目录不可写（位置被文件占位）：setup_logging 降级不抛（R3-M-04 回归）。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("file occupies app_dir", encoding="utf-8")
    code = (
        "import sys, pathlib;"
        f"sys.path.insert(0, {str(PROJECT_ROOT)!r});"
        "import gold_pet.main as m;"
        f"m.app_dir = lambda: pathlib.Path({str(blocker)!r});"
        "m.setup_logging('INFO');"
        "print('degraded-ok')"
    )
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(PROJECT_ROOT),
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "degraded-ok" in proc.stdout


def test_r3_degraded_control_disabled_and_tray_absent(tmp_path, monkeypatch):
    """控制服务关闭 + 托盘不可用：窗口照常工作，quit 路径按无托盘语义。"""
    from gold_pet import ui as ui_mod

    w = _window(tmp_path, monkeypatch=monkeypatch)
    try:
        assert w.control is None
        tray = ui_mod.build_tray(w, w.cfg)
        if tray is None:
            assert w.tray_icon() is None
        w._render(_good_snapshot(), silent=True)
        assert "897.15" in w.bubble.text()
    finally:
        w.deleteLater()


def test_r3_degraded_cli_without_any_data_files(tmp_path, monkeypatch, capsys):
    """全新机器（无任何数据文件）：history/alerts 命令结构化失败，退出码 ∈ {3,4}。"""
    monkeypatch.setattr(cli_mod, "cache_path", lambda: tmp_path / "last_price.json")
    monkeypatch.setattr(cli_mod, "history_path", lambda: tmp_path / "history.json")
    monkeypatch.setattr(cli_mod, "alert_history_path", lambda: tmp_path / "alert_history.json")
    monkeypatch.setattr(cli_mod, "control_info_path", lambda: tmp_path / "control.json")
    monkeypatch.setattr(config_mod, "settings_path", lambda: tmp_path / "settings.json")
    assert cli_mod.main(["history", "--json"]) == 4
    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["ok"] is False and data["error"]["exit_code"] == 4
    assert cli_mod.main(["alerts", "--json"]) == 4
    data2 = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data2["ok"] is False
