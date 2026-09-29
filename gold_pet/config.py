r"""配置读写：JSON 存在用户目录，方便手动修改。

Windows: %APPDATA%\\GoldPet\\settings.json
其他系统: ~/.gold-pet/settings.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import app_dir, asset_dir, settings_path  # noqa: F401

DEFAULTS: dict = {
    "metal": "gold",  # gold = 黄金, silver = 白银, platinum = 铂金
    "unit": "cny_g",  # cny_g = 元/克, usd_oz = 美元/盎司
    "interval_sec": 60,  # 刷新间隔（秒）
    "alert_pct": 0.5,  # 涨跌幅提醒阈值（%）
    "alert_cooldown_sec": 600,  # 同类提醒冷却时间（秒）
    "target_high": 0.0,  # 到价提醒：金价 ≥ 该值（元/克）时提醒，0 = 关闭
    "target_low": 0.0,  # 到价提醒：金价 ≤ 该值（元/克）时提醒，0 = 关闭
    "always_on_top": True,  # 窗口置顶
    "opacity": 1.0,  # 不透明度 0.3 ~ 1.0
    "show_bubble": True,  # 是否显示价格气泡
    "show_spark": True,  # 是否显示迷你走势图
    "autostart": False,  # 开机自启
    "pos": None,  # 窗口位置 [x, y]，None 表示右下角默认位
}

INTERVAL_CHOICES = [15, 30, 60, 300, 900]

METAL_NAMES = {"gold": "黄金", "silver": "白银", "platinum": "铂金"}
UNIT_NAMES = {"cny_g": "元/克", "usd_oz": "美元/盎司"}


def _as_float(value: Any, default: float) -> float:
    """把任意值转成 float，失败回落默认值（配置文件可能被手改坏）。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    return int(_as_float(value, float(default)))


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


class Config:
    """简单配置对象，属性即键。"""

    def __init__(self, data: dict | None = None):
        self._data = dict(DEFAULTS)
        if data:
            self._data.update({k: v for k, v in data.items() if k in DEFAULTS})

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or settings_path()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        cfg = cls(data)
        cfg._path = path  # type: ignore[attr-defined]
        cfg.sanitize()
        return cfg

    def sanitize(self) -> None:
        """把配置收敛到合法范围：手改坏了也不让程序崩。"""
        d = self._data
        if d.get("metal") not in METAL_NAMES:
            d["metal"] = DEFAULTS["metal"]
        if d.get("unit") not in UNIT_NAMES:
            d["unit"] = DEFAULTS["unit"]

        interval = _as_int(d.get("interval_sec"), DEFAULTS["interval_sec"])
        d["interval_sec"] = interval if interval in INTERVAL_CHOICES else DEFAULTS["interval_sec"]
        d["alert_pct"] = _clamp(_as_float(d.get("alert_pct"), 0.5), 0.0, 20.0)
        d["alert_cooldown_sec"] = _clamp(_as_int(d.get("alert_cooldown_sec"), 600), 60, 7200)
        d["opacity"] = _clamp(_as_float(d.get("opacity"), 1.0), 0.3, 1.0)

        pos = d.get("pos")
        if not (isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, int) for v in pos)):
            d["pos"] = None

        for key in ("target_high", "target_low"):
            value = _as_float(d.get(key), 0.0)
            d[key] = value if value > 0 else 0.0

        for key in ("always_on_top", "show_bubble", "show_spark", "autostart"):
            d[key] = bool(d.get(key, DEFAULTS[key]))

    def save(self) -> None:
        path = getattr(self, "_path", None) or settings_path()
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
