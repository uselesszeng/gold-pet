r"""配置读写：JSON 存在用户目录，方便手动修改。

Windows: %APPDATA%\\GoldPet\\settings.json
其他系统: ~/.gold-pet/settings.json
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from .paths import app_dir, asset_dir, settings_path  # noqa: F401
from .price import _atomic_write_text

logger = logging.getLogger("gold_pet")

DEFAULTS: dict = {
    "metal": "gold",  # gold = 黄金, silver = 白银, platinum = 铂金
    "unit": "cny_g",  # cny_g = 元/克, usd_oz = 美元/盎司
    "interval_sec": 60,  # 刷新间隔（秒）
    "alert_pct": 0.5,  # 涨跌幅提醒阈值（%）
    "alert_cooldown_sec": 600,  # 重复提醒防抖间隔（秒）
    "alert_extreme_step_pct": 0.1,  # 新高新低最小步进（%），0 = 任何新高都提醒
    "target_high": 0.0,  # 到价提醒：金价 ≥ 该值（元/克）时提醒，0 = 关闭
    "target_low": 0.0,  # 到价提醒：金价 ≤ 该值（元/克）时提醒，0 = 关闭
    # 三类提醒的独立开关（v0.5 提醒中心）
    "alert_pct_enabled": True,
    "alert_target_enabled": True,
    "alert_extreme_enabled": True,
    # 勿扰模式：时段内只静音系统通知，气泡与走势图照常
    "dnd_enabled": False,
    "dnd_start": "23:00",
    "dnd_end": "08:00",
    # 迷你走势图时间范围：1h / today / all
    "spark_range": "1h",
    # 动作活跃度：quiet = 安静(完全静止，系数 0) / normal = 标准 / lively = 活泼(×0.7)
    "activity_level": "normal",
    # 关怀提醒（v0.7）：只走鲸娘气泡，不弹系统通知、不进提醒中心
    "care_drink_enabled": True,  # 喝水提醒（默认开）
    "care_drink_interval_min": 60,
    "care_posture_enabled": False,  # 久坐提醒
    "care_posture_interval_min": 90,
    "care_eye_enabled": False,  # 护眼提醒
    "care_eye_interval_min": 45,
    # 托盘图标是否显示价格数字
    "tray_show_price": True,
    # AI 控制服务（v0.8）：本地 HTTP 仅绑 127.0.0.1 随机端口 + token 鉴权，
    # 供 MCP 桥接进程（tools/pet_mcp_server.py）远程操控鲸娘
    "control_enabled": True,
    "always_on_top": True,  # 窗口置顶
    "opacity": 1.0,  # 不透明度 0.3 ~ 1.0
    "show_bubble": True,  # 是否显示价格气泡
    "show_spark": True,  # 是否显示迷你走势图
    "autostart": False,  # 开机自启
    "pos": None,  # 窗口位置 [x, y]，None 表示右下角默认位
}

INTERVAL_CHOICES = [15, 30, 60, 300, 900]

# 走势图时间范围：键 -> 展示名
SPARK_RANGE_NAMES = {"1h": "1 小时", "today": "今日", "all": "全部"}

# 动作活跃度：键 -> (展示名, 帧间隔系数)；系数 0 表示完全静止（安静档，v0.7.2）
ACTIVITY_LEVELS = {
    "quiet": ("😴 安静", 0.0),
    "normal": ("🙂 标准", 1.0),
    "lively": ("🤩 活泼", 0.7),
}

# 关怀提醒间隔可选值（分钟）
CARE_INTERVAL_CHOICES = [30, 45, 60, 90, 120]

METAL_NAMES = {"gold": "黄金", "silver": "白银", "platinum": "铂金"}
UNIT_NAMES = {"cny_g": "元/克", "usd_oz": "美元/盎司"}


def _as_float(value: Any, default: float) -> float:
    """把任意值转成 float，失败回落默认值（配置文件可能被手改坏）。

    M-03：JSON 文件里手写的 NaN / Infinity 会被 json.loads 解析成 float("nan"/"inf")，
    后续 int(nan) 抛 ValueError、int(inf) 抛 OverflowError 直接炸穿 sanitize()/load()，
    导致程序起不来；clamp 对 NaN 也不生效（结果还是 NaN）。非有限值一律视为坏值回落默认。
    R2-M-01：json.loads 对 400 位以上的整数字面量产出任意精度 int，float(10**400)
    抛的是 OverflowError（不属于 ValueError），同样必须接住回落默认。
    """
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(result):
        return default
    return result


def _as_int(value: Any, default: int) -> int:
    return int(_as_float(value, float(default)))


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _valid_hhmm(text: str) -> bool:
    """校验 "HH:MM" 格式是否合法（供勿扰时段使用）。"""
    parts = text.strip().split(":")
    if len(parts) != 2:
        return False
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return 0 <= hour <= 23 and 0 <= minute <= 59


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
        except (OSError, ValueError, RecursionError):
            # RecursionError：R2-M-03，手改坏的超深嵌套 settings.json（2 万层）
            # 会让 json.loads 抛 RecursionError，不接住程序直接拒启动
            data = {}
        cfg = cls(data)
        cfg._path = path  # type: ignore[attr-defined]
        cfg.sanitize()
        return cfg

    def sanitize(self) -> None:
        """把配置收敛到合法范围：手改坏了也不让程序崩。"""
        d = self._data
        # M-10：值可能是 list/dict 等不可哈希类型，dict 成员判断会抛
        # TypeError: unhashable type，必须先验 isinstance(str)。
        if not isinstance(d.get("metal"), str) or d["metal"] not in METAL_NAMES:
            d["metal"] = DEFAULTS["metal"]
        if not isinstance(d.get("unit"), str) or d["unit"] not in UNIT_NAMES:
            d["unit"] = DEFAULTS["unit"]

        interval = _as_int(d.get("interval_sec"), DEFAULTS["interval_sec"])
        d["interval_sec"] = interval if interval in INTERVAL_CHOICES else DEFAULTS["interval_sec"]
        d["alert_pct"] = _clamp(_as_float(d.get("alert_pct"), 0.5), 0.0, 20.0)
        d["alert_cooldown_sec"] = _clamp(_as_int(d.get("alert_cooldown_sec"), 600), 60, 7200)
        d["alert_extreme_step_pct"] = _clamp(
            _as_float(d.get("alert_extreme_step_pct"), 0.1), 0.0, 5.0
        )
        d["opacity"] = _clamp(_as_float(d.get("opacity"), 1.0), 0.3, 1.0)

        pos = d.get("pos")
        if not (isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, int) for v in pos)):
            d["pos"] = None

        for key in ("target_high", "target_low"):
            value = _as_float(d.get(key), 0.0)
            d[key] = value if value > 0 else 0.0

        bool_keys = (
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
        )
        for key in bool_keys:
            d[key] = bool(d.get(key, DEFAULTS[key]))

        # 走势图时间范围：非法值回落默认（M-10：先验 str 再做成员判断）
        if not isinstance(d.get("spark_range"), str) or d["spark_range"] not in SPARK_RANGE_NAMES:
            d["spark_range"] = DEFAULTS["spark_range"]

        # 动作活跃度：非法值回落标准（M-10：同上）
        if (
            not isinstance(d.get("activity_level"), str)
            or d["activity_level"] not in ACTIVITY_LEVELS
        ):
            d["activity_level"] = DEFAULTS["activity_level"]

        # 关怀提醒：开关布尔化，间隔收敛到可选档位
        for care_key in ("care_drink_enabled", "care_posture_enabled", "care_eye_enabled"):
            d[care_key] = bool(d.get(care_key, DEFAULTS[care_key]))
        for interval_key in (
            "care_drink_interval_min",
            "care_posture_interval_min",
            "care_eye_interval_min",
        ):
            raw = _as_int(d.get(interval_key), DEFAULTS[interval_key])
            d[interval_key] = raw if raw in CARE_INTERVAL_CHOICES else DEFAULTS[interval_key]

        # 勿扰时间：必须是合法 "HH:MM"，否则回落默认
        # 注意：变量名不能复用上面的 value（mypy 已将其推断为 float）
        for hhmm_key in ("dnd_start", "dnd_end"):
            hhmm_value = d.get(hhmm_key)
            if not isinstance(hhmm_value, str) or not _valid_hhmm(hhmm_value):
                d[hhmm_key] = DEFAULTS[hhmm_key]

    def save(self) -> None:
        """把配置写回磁盘；失败只记警告不抛（R3-M-03）。

        与 save_cache / Notifier._save / HistoryStore.append 的容错口径一致：
        磁盘满/只读/文件被占时配置保存属最佳努力，绝不能让一次写盘失败
        炸穿 UI 槽——quit_app 里的 save 抛 OSError 会让 QApplication.quit()
        永不执行（托盘退出/关窗退不掉）。

        R5-M-01：落盘必须原子。裸 write_text 是 truncate→写入，中间存在
        「文件为 0 字节或半截 JSON」的窗口——并发的 CLI/同步盘/杀软读到会
        JSONDecodeError，窗口内进程被杀则配置永久留在截断态、用户自定义项
        静默蒸发。改用与其余三个写入器同源的 _atomic_write_text（tmp + os.replace）。
        """
        path = getattr(self, "_path", None) or settings_path()
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(Path(path), json.dumps(self._data, ensure_ascii=False, indent=2))
        except OSError:
            logger.warning("settings: 配置保存失败（%s），本次改动不落盘", path, exc_info=True)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def as_dict(self) -> dict:
        return dict(self._data)
