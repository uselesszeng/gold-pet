"""配置读写：JSON 存在用户目录，方便手动修改。

Windows: %APPDATA%\\GoldPet\\settings.json
其他系统: ~/.gold-pet/settings.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "GoldPet"

DEFAULTS: dict = {
    "metal": "gold",            # gold = 黄金, silver = 白银, platinum = 铂金
    "unit": "cny_g",            # cny_g = 元/克, usd_oz = 美元/盎司
    "interval_sec": 60,         # 刷新间隔（秒）
    "alert_pct": 0.5,           # 涨跌幅提醒阈值（%）
    "always_on_top": True,      # 窗口置顶
    "opacity": 1.0,             # 不透明度 0.3 ~ 1.0
    "show_bubble": True,        # 是否显示价格气泡
    "show_spark": True,         # 是否显示迷你走势图
    "autostart": False,         # 开机自启
    "pos": None,                # 窗口位置 [x, y]，None 表示右下角默认位
}

INTERVAL_CHOICES = [15, 30, 60, 300, 900]

METAL_NAMES = {"gold": "黄金", "silver": "白银", "platinum": "铂金"}
UNIT_NAMES = {"cny_g": "元/克", "usd_oz": "美元/盎司"}


def app_dir() -> Path:
    """返回配置目录（不存在则创建）。"""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
        path = base / APP_NAME
    else:
        path = Path.home() / f".{APP_NAME.lower()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def asset_dir() -> Path:
    """素材目录（源码包内的 assets）。"""
    return Path(__file__).resolve().parent.parent / "assets"


class Config:
    """简单配置对象，属性即键。"""

    def __init__(self, data: dict | None = None):
        self._data = dict(DEFAULTS)
        if data:
            self._data.update({k: v for k, v in data.items() if k in DEFAULTS})

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or app_dir() / "settings.json"
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        cfg = cls(data)
        cfg._path = path  # type: ignore[attr-defined]
        return cfg

    def save(self) -> None:
        path = getattr(self, "_path", None) or app_dir() / "settings.json"
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def as_dict(self) -> dict:
        return dict(self._data)
