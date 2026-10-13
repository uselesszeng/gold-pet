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


# REVIEW5-02：布尔键的「常见真/假写法」白名单（大小写不敏感、先去空白）。
_TRUE_STRINGS = {"1", "true", "yes", "on"}
_FALSE_STRINGS = {"0", "false", "no", "off"}


def _as_bool(value: Any, default: bool) -> bool:
    """把任意值收敛成 bool——按**语义**而非 Python truthiness（REVIEW5-02）。

    旧口径 `bool(值)`：非空字符串恒为 True，于是用户在 settings.json 里手写的
    `"control_enabled": "false"`（**字符串**）被当成 True——用户的「关闭」意图被
    静默反转成「开启」并落盘，用户看到的现象是「开关不管用」。实测四个键
    （control_enabled / show_bubble / dnd_enabled / always_on_top）全部中招。

    现在的口径：
      bool 原样；int 0=False 非0=True（锚定既有语义）；float 非有限值回落默认、
      其余按是否为 0；字符串认常见真/假写法，其它未知字符串回落默认；
      None/容器等一律回落该键默认值。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, float):
        if not math.isfinite(value):
            return default
        return value != 0.0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_STRINGS:
            return True
        if text in _FALSE_STRINGS:
            return False
        return default
    return default


def _backup_corrupt(path: Path, raw: bytes) -> None:
    """受损配置留档（REVIEW5-01）：告警 + 原始字节写入 `<文件>.corrupt`。

    只落一份**最早**现场（已存在不覆盖）：程序反复重启时若把不同坏内容轮换着
    覆盖，反倒看不清是哪一次坏的、首因是什么。按字节留档（而非解码后写文本），
    非 UTF-8 的坏文件也能原样保存，供人工用编辑器找回。

    写入失败只记日志、不再扩散异常——「坏配置也能起默认态」这条底线不能破。
    """
    backup = path.with_name(path.name + ".corrupt")
    logger.warning(
        "settings: %s 内容损坏（非 UTF-8 / JSON 非法 / 顶层不是对象），本次以默认配置运行；"
        "原始字节已留档到 %s，可人工找回后修复",
        path,
        backup,
    )
    try:
        if not backup.exists():
            backup.write_bytes(raw)
    except OSError:
        logger.warning("settings: 损坏配置留档失败", exc_info=True)


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
        """读取配置；**区分「不存在 / 读失败 / 内容损坏」三态**（REVIEW5-01）。

        旧口径把三态挤进同一条 `except` 返回 `{}`：无告警、无留档。后果是一条
        完整的用户数据丢失链——用户手改坏一个字符 → 静默整份归零 → 程序退出时
        `save()` 用默认值覆写原文件 → 金属/单位/阈值/提醒/勿扰设置**永久丢失且
        无法追回**。实测 `{"metal":"silver",...}` 少一个花括号即复现。

        现在：文件不存在静默走默认（首次启动，正常）；读取失败告警；解析失败或
        顶层不是对象时除告警外把**原始字节**留档到 `<文件>.corrupt`（保留最早现场）。
        """
        path = path or settings_path()
        data: Any
        try:
            raw = Path(path).read_bytes()
        except FileNotFoundError:
            data = {}
        except OSError:
            logger.warning(
                "settings: 配置文件读取失败（%s），本次按默认配置运行", path, exc_info=True
            )
            data = {}
        else:
            # 先按字节读再解码：非 UTF-8 文件（GBK 手改/二进制垃圾）的
            # UnicodeDecodeError 也是 ValueError 子类，必须与解析失败同口径，
            # 不能漏出 load()（老代码在 except ValueError 里顺带接住了它）。
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                _backup_corrupt(path, raw)
                text = None
            if text is None:
                data = {}
            else:
                try:
                    parsed = json.loads(text)
                except (ValueError, RecursionError):
                    # RecursionError：R2-M-03，手改坏的超深嵌套 settings.json（2 万层）
                    # 会让 json.loads 抛 RecursionError，不接住程序直接拒启动
                    parsed = None
                if isinstance(parsed, dict):
                    data = parsed
                else:
                    _backup_corrupt(path, raw)
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
        # REVIEW5-02：_as_bool 按语义收敛（"false"/"0" 不再被真值判定反转成
        # True，未知字符串/容器/非有限数回落各键默认）
        for key in bool_keys:
            d[key] = _as_bool(d.get(key), DEFAULTS[key])

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
        # REVIEW5-02 同族：三个关怀提醒开关必须与主 bool_keys 同一口径
        for care_key in ("care_drink_enabled", "care_posture_enabled", "care_eye_enabled"):
            d[care_key] = _as_bool(d.get(care_key), DEFAULTS[care_key])
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
        """把配置写回磁盘；失败只记警告不抛（R3-M-03），且必须**原子写**（REVIEW5-03）。

        容错口径与 save_cache / Notifier._save / HistoryStore.append 一致：
        磁盘满/只读/文件被占时配置保存属最佳努力，绝不能让一次写盘失败炸穿 UI 槽
        ——quit_app 里的 save 抛 OSError 会让 QApplication.quit() 永不执行
        （托盘退出/关窗退不掉）。

        REVIEW5-03：旧实现是**直写**（truncate → write）。写入中途失败（ENOSPC/
        进程被杀）会在磁盘上留下**半截 JSON**，下次启动 `load()` 读不动 →
        触发 REVIEW5-01 的整份归零 → 变成「一次写入失败 = 用户配置永久丢失」的
        连锁（已实测复现半截文件）。改走与其余三个写入器同一套 `_atomic_write_text`
        （tmp + os.replace）：读者要么看到旧内容要么看到完整新内容，绝无半截。
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
