"""提醒规则与提醒历史（纯逻辑，不依赖 Qt）。

把三类提醒从 ui.py 里抽出来，统一成「规则判定 → 产出 Alert → 交给界面展示」的流程：

  涨跌幅提醒（pct）    现价相对昨收的涨跌幅超过阈值
  到价提醒（target）   现价突破用户设定的目标价（以元/克为基准）
  创新高/新低（extreme）现价触及当日最高 / 最低

同时负责：
  - 同类提醒的冷却去重（同一方向在冷却期内只提醒一次）
  - 勿扰时段判断（跨零点也支持）
  - 提醒历史的读写（JSON，保留最近 N 条）

设计原则与 price.py 一致：纯函数 + 可注入时钟，方便单测。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .price import Quote

# 提醒类型
KIND_PCT = "pct"
KIND_TARGET = "target"
KIND_EXTREME = "extreme"

KIND_NAMES = {
    KIND_PCT: "涨跌幅提醒",
    KIND_TARGET: "到价提醒",
    KIND_EXTREME: "新高新低提醒",
}


@dataclass
class Alert:
    """一条待展示的提醒。"""

    kind: str  # pct / target / extreme
    side: str  # up / down / high / low
    title: str
    body: str
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "side": self.side,
            "title": self.title,
            "body": self.body,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Alert:
        return cls(
            kind=data.get("kind", ""),
            side=data.get("side", ""),
            title=data.get("title", ""),
            body=data.get("body", ""),
            at=float(data.get("at", 0.0)),
        )

    @property
    def kind_name(self) -> str:
        return KIND_NAMES.get(self.kind, self.kind)


def in_quiet_hours(
    now_minutes: int | None = None,
    start: str = "",
    end: str = "",
) -> bool:
    """判断当前是否处于勿扰时段。

    start / end 为 "HH:MM" 文本（来自配置文件，可能被手改坏）。
    支持跨零点（如 23:00 - 08:00）。非法或起止相同时返回 False（视为未启用）。
    """
    begin = parse_hhmm(start)
    finish = parse_hhmm(end)
    if begin is None or finish is None or begin == finish:
        return False
    if now_minutes is None:
        local = time.localtime()
        now_minutes = local.tm_hour * 60 + local.tm_min
    if begin < finish:  # 同日区间，如 12:00-14:00
        return begin <= now_minutes < finish
    # 跨零点区间，如 23:00-08:00
    return now_minutes >= begin or now_minutes < finish


def parse_hhmm(text: str) -> int | None:
    """把 "HH:MM" 解析为当日分钟数；格式非法返回 None。"""
    if not isinstance(text, str):
        return None
    parts = text.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def format_hhmm(minutes: int) -> str:
    """把当日分钟数格式化为 "HH:MM"。"""
    minutes = max(0, min(int(minutes), 23 * 60 + 59))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class Notifier:
    """三类提醒的规则判定 + 冷却去重 + 历史记录。

    用法：每次刷新成功后调用 evaluate()，拿到 Alert 列表再决定如何展示。
    """

    def __init__(
        self,
        cfg,
        history_path: Path | None = None,
        max_history: int = 200,
    ):
        self.cfg = cfg
        self.history_path = Path(history_path) if history_path else None
        self.max_history = max_history
        # 冷却状态：kind -> (最近触发时间, 方向)
        self._last: dict[str, tuple[float, str]] = {}
        self._history: list[Alert] = []
        self._loaded = False

    # ------------------------------------------------------------ 冷却判定
    def _cooled_down(self, kind: str, side: str, now: float) -> bool:
        """同类型同方向在冷却期内视为重复，不再提醒。"""
        cooldown = int(self.cfg.get("alert_cooldown_sec", 600))
        last_at, last_side = self._last.get(kind, (0.0, ""))
        return now - last_at < cooldown and side == last_side

    def _mark(self, kind: str, side: str, now: float) -> None:
        self._last[kind] = (now, side)

    def _enabled(self, kind: str) -> bool:
        """三类提醒各自的总开关（默认开）。"""
        return bool(self.cfg.get(f"alert_{kind}_enabled", True))

    # ------------------------------------------------------------ 规则判定
    def evaluate(
        self,
        quote: Quote,
        metal_name: str = "黄金",
        base_quote: Quote | None = None,
        now: float | None = None,
    ) -> list[Alert]:
        """对一次成功刷新的行情做全部规则判定，返回本次应触发的提醒。

        quote      当前展示的行情（可能是换算后的）
        base_quote 元/克 基准行情，用于到价提醒（与展示单位无关）
        """
        now = now if now is not None else time.time()
        alerts: list[Alert] = []
        alerts += self._check_pct(quote, metal_name, now)
        alerts += self._check_extreme(quote, metal_name, now)
        alerts += self._check_target(base_quote or quote, metal_name, now)
        return alerts

    def _check_pct(self, quote: Quote, metal_name: str, now: float) -> list[Alert]:
        if not self._enabled(KIND_PCT):
            return []
        threshold = float(self.cfg.get("alert_pct", 0.5))
        pct = quote.change_pct
        if threshold <= 0 or abs(pct) < threshold:
            return []
        side = "up" if pct > 0 else "down"
        if self._cooled_down(KIND_PCT, side, now):
            return []
        self._mark(KIND_PCT, side, now)
        arrow = "上涨" if pct > 0 else "下跌"
        return [
            Alert(
                kind=KIND_PCT,
                side=side,
                title="黄金桌宠提醒",
                body=f"{metal_name}价{arrow} {abs(pct):.2f}%，已超过设定阈值 {threshold:.2f}%",
                at=now,
            )
        ]

    def _check_extreme(self, quote: Quote, metal_name: str, now: float) -> list[Alert]:
        if not self._enabled(KIND_EXTREME):
            return []
        if quote.high <= 0 and quote.low <= 0:
            return []
        if quote.high > 0 and quote.price >= quote.high:
            side, title = "high", "创新高"
        elif quote.low > 0 and quote.price <= quote.low:
            side, title = "low", "创新低"
        else:
            return []
        if self._cooled_down(KIND_EXTREME, side, now):
            return []
        self._mark(KIND_EXTREME, side, now)
        return [
            Alert(
                kind=KIND_EXTREME,
                side=side,
                title="黄金桌宠提醒",
                body=(
                    f"{metal_name}{title}：{quote.format_price()} {quote.unit}"
                    f"（今日高 {quote.high:.2f} / 低 {quote.low:.2f}）"
                ),
                at=now,
            )
        ]

    def _check_target(self, base: Quote, metal_name: str, now: float) -> list[Alert]:
        """到价提醒：以元/克为基准，与当前展示单位无关。"""
        if not self._enabled(KIND_TARGET):
            return []
        high = float(self.cfg.get("target_high", 0.0))
        low = float(self.cfg.get("target_low", 0.0))
        if high <= 0 and low <= 0:
            return []
        if not base.unit.startswith("元"):
            return []  # 拿不到元/克基准就不判断，避免单位错配
        if high > 0 and base.price >= high:
            side, body = "high", f"金价已涨到 {base.price:.2f} 元/克（目标 {high:.2f}）"
        elif low > 0 and base.price <= low:
            side, body = "low", f"金价已跌到 {base.price:.2f} 元/克（目标 {low:.2f}）"
        else:
            return []
        if self._cooled_down(KIND_TARGET, side, now):
            return []
        self._mark(KIND_TARGET, side, now)
        return [Alert(kind=KIND_TARGET, side=side, title="黄金桌宠 · 到价提醒", body=body, at=now)]

    # ------------------------------------------------------------ 勿扰模式
    def in_quiet_hours(self, now_minutes: int | None = None) -> bool:
        """当前是否处于勿扰时段（勿扰只静音系统通知，不影响气泡）。"""
        if not bool(self.cfg.get("dnd_enabled", False)):
            return False
        return in_quiet_hours(
            now_minutes,
            self.cfg.get("dnd_start", "23:00"),
            self.cfg.get("dnd_end", "08:00"),
        )

    # ------------------------------------------------------------ 历史
    def record(self, alerts: list[Alert]) -> None:
        """把提醒追加到历史（内存 + 落盘）。"""
        if not alerts:
            return
        self._ensure_loaded()
        self._history.extend(alerts)
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history :]
        self._save()

    def history(self, kind: str | None = None) -> list[Alert]:
        """取提醒历史（按时间倒序）。kind 为 None 时返回全部。"""
        self._ensure_loaded()
        items = [a for a in self._history if kind is None or a.kind == kind]
        return sorted(items, key=lambda a: a.at, reverse=True)

    def clear_history(self) -> None:
        self._history = []
        self._loaded = True
        self._save()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.history_path is None:
            return
        try:
            raw = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, list):
            return
        self._history = [Alert.from_dict(item) for item in raw if isinstance(item, dict)][
            -self.max_history :
        ]

    def _save(self) -> None:
        if self.history_path is None:
            return
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self.history_path.write_text(
                json.dumps([a.to_dict() for a in self._history], ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError:
            pass


# ------------------------------------------------------------------ 走势范围

SPARK_RANGES = {
    "1h": ("1 小时", 3600),
    "today": ("今日", None),  # 由本地零点推算
    "all": ("全部", None),
}


def spark_cutoff(range_key: str, now: float | None = None) -> float | None:
    """返回走势图的时间下界（None 表示不限制）。

    - "1h"     → 当前时间往前 1 小时
    - "today"  → 本机当天 00:00
    - "all"    → 不限制
    """
    if range_key == "1h":
        return (now if now is not None else time.time()) - 3600
    if range_key == "today":
        local = time.localtime(now if now is not None else time.time())
        return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
    return None


def filter_series(series: list[dict], range_key: str, now: float | None = None) -> list[float]:
    """按时间范围过滤历史序列，返回价格列表。

    series 形如 [{"t": 时间戳, "p": 价格}, ...]；无有效点时返回空列表。
    """
    cutoff = spark_cutoff(range_key, now)
    if cutoff is None:
        return [float(p.get("p", 0.0)) for p in series]
    return [float(p.get("p", 0.0)) for p in series if float(p.get("t", 0.0)) >= cutoff]
