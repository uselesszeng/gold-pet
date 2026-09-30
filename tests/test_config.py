"""配置读写与素材生成的单元测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gold_pet.config import (  # noqa: E402
    DEFAULTS,
    METAL_NAMES,
    UNIT_NAMES,
    Config,
    asset_dir,
)


def test_defaults_are_complete():
    cfg = Config()
    for k in ("metal", "unit", "interval_sec", "alert_pct", "opacity", "pos"):
        assert k in cfg.as_dict()
    assert cfg.get("metal") == "gold"
    assert cfg.get("unit") == "cny_g"
    assert cfg.get("interval_sec") == 60


def test_load_unknown_keys_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"unit": "usd_oz", "hack": 1}), encoding="utf-8")
    cfg = Config.load(path)
    assert cfg.get("unit") == "usd_oz"
    assert "hack" not in cfg.as_dict()


def test_load_broken_file_falls_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("not json", encoding="utf-8")
    cfg = Config.load(path)
    assert cfg.get("metal") == DEFAULTS["metal"]


def test_save_and_reload(tmp_path):
    path = tmp_path / "settings.json"
    cfg = Config.load(path)
    cfg.set("metal", "silver")
    cfg.set("alert_pct", 1.5)
    cfg.save()
    again = Config.load(path)
    assert again.get("metal") == "silver"
    assert again.get("alert_pct") == 1.5


def test_config_sanitize_bad_values(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "interval_sec": "abc",
                "opacity": 99,
                "metal": "xxx",
                "unit": "bad",
                "pos": [1],
                "alert_pct": 500,
                "alert_cooldown_sec": -5,
            }
        ),
        encoding="utf-8",
    )
    cfg = Config.load(path)
    assert cfg.get("interval_sec") == 60
    assert cfg.get("opacity") == 1.0
    assert cfg.get("metal") == "gold"
    assert cfg.get("unit") == "cny_g"
    assert cfg.get("pos") is None
    assert cfg.get("alert_pct") == 20.0
    assert 60 <= cfg.get("alert_cooldown_sec") <= 7200


def test_names_mapping():
    assert METAL_NAMES["gold"] == "黄金"
    assert UNIT_NAMES["usd_oz"] == "美元/盎司"


def test_activity_and_care_sanitize(tmp_path):
    """v0.7：活跃度非法值回落标准；关怀间隔收敛到可选档位、开关布尔化。"""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "activity_level": "hyper",  # 非法 -> normal
                "care_drink_interval_min": 77,  # 不在档位 -> 60
                "care_posture_enabled": 1,  # -> True
                "care_eye_interval_min": "abc",  # 解析失败 -> 45
            }
        ),
        encoding="utf-8",
    )
    cfg = Config.load(path)
    assert cfg.get("activity_level") == "normal"
    assert cfg.get("care_drink_enabled") is True  # 默认开
    assert cfg.get("care_drink_interval_min") == 60
    assert cfg.get("care_posture_enabled") is True
    assert cfg.get("care_posture_interval_min") == 90
    assert cfg.get("care_eye_enabled") is False  # 默认关
    assert cfg.get("care_eye_interval_min") == 45


def test_assets_exist():
    pet = asset_dir() / "pet"
    assert (asset_dir() / "icon.png").exists()
    for name in (
        "idle_0",
        "idle_1",
        "idle_2",
        "idle_3",
        "up_0",
        "down_0",
        "pat_0",
        "shy_0",
        "loading_0",
        "offline_0",
    ):
        assert (pet / f"{name}.png").exists(), name
