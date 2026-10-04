"""提醒规则与提醒历史（纯逻辑，不依赖 Qt）。

把三类提醒从 ui.py 里抽出来，统一成「规则判定 → 产出 Alert → 交给界面展示」的流程：

  涨跌幅提醒（pct）    现价相对昨收的涨跌幅超过阈值
  到价提醒（target）   现价突破用户设定的目标价（以元/克为基准）
  创新高/新低（extreme）现价触及并刷新当日最高 / 最低

同时负责：
  - 状态变化去重（v0.5.6，详见下）
  - 勿扰时段判断（跨零点也支持）
  - 提醒历史的读写（JSON，保留最近 N 条）

设计原则与 price.py 一致：纯函数 + 可注入时钟，方便单测。

v0.5.6「状态变化才提醒」语义
---------------------------
v0.5.5 及以前，冷却只按「类型 + 方向」判断，只要方向没变，冷却时间一到就再弹一次。
价格横盘时（今日高 = 现价、涨跌幅恒定）会出现「同一句话每 10 分钟弹一次」，用户看到
的是无意义的刷屏。v0.5.6 把去重依据从「时间」换成「状态是否变化」：

  涨跌幅   按阈值分档（0.5% / 1.0% / 1.5% …），只有跨入更高档位才再次提醒。
           横盘在 +1.14% 只提醒一次；涨到 +1.6% 再提醒一次。跌破阈值后重新武装。
  新高新低 记住当日已播报的水位，只有严格越过水位才算「创新高 / 新低」。
           水位单调递增（递减），因此天然不会重复。
  到价提醒 只在「从目标价外进入目标价内」的那一刻提醒一次；回到目标价外后重新武装。

alert_cooldown_sec 退居为「防抖」：只拦截「跌破阈值后又迅速越线」这类抖动，
不再负责拦截「内容完全相同的重复提醒」——后者现在由状态判定根本性地挡掉。
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .price import Quote, _atomic_write_text

logger = logging.getLogger("gold_pet")


def _safe_float(value, default: float | None = None) -> float | None:
    """宽松转 float：失败返回 default（P2-03）。

    default 传 0.0 表示「坏值按 0 兜底」；保持 None 供调用方识别坏值并跳过。
    M-05：NaN / Infinity 能通过 float()，但会污染下游（走势图拿到 NaN 点、
    提醒历史 json.dumps 写出非法的 `NaN` 字面量），与转换失败同等对待。
    R2-M-01：float(10**400) 抛 OverflowError（JSON 任意精度整数字面量），
    同样必须接住，否则脏 history.json 会炸穿 filter_series/渲染链路。
    """
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(result):
        return default
    return result

# 提醒类型
KIND_PCT = "pct"
KIND_TARGET = "target"
KIND_EXTREME = "extreme"

KIND_NAMES = {
    KIND_PCT: "涨跌幅提醒",
    KIND_TARGET: "到价提醒",
    KIND_EXTREME: "新高新低提醒",
}

# 影响提醒判定的配置项：其中任一项被用户改动，都应重置去重状态，
# 否则旧状态（如旧阈值算出的档位）会与新规则对不上。
RULE_CONFIG_KEYS = (
    "metal",
    "unit",
    "alert_pct",
    "alert_cooldown_sec",
    "alert_extreme_step_pct",
    "target_high",
    "target_low",
    "alert_pct_enabled",
    "alert_target_enabled",
    "alert_extreme_enabled",
)


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
            # P2-03：at 非数值/缺失时按 0 兜底，不让脏历史炸掉加载链路
            at=_safe_float(data.get("at"), 0.0),
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
    """三类提醒的规则判定 + 状态去重 + 历史记录。

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
        # ---- 状态去重（v0.5.6）----
        # 涨跌幅：上次播报的 (方向, 档位)；None 表示当前不在阈值区间内
        self._pct_seen: tuple[str, int] | None = None
        # 涨跌幅：上次播报时间；None 表示本次会话还没播报过（不能用 0.0，
        # 否则注入小时钟的单元测试会把「从未播报」误判成「刚播报过」）
        self._pct_at: float | None = None
        # 新高新低：当日已播报的水位，如 {"high": 907.6, "low": 896.33}
        self._extreme_water: dict[str, float] = {}
        # 到价提醒：是否已处于生效态，如 {"high": True}
        self._target_state: dict[str, bool] = {}
        # 当前日期字符串，用于跨日重置水位
        self._day: str = ""
        self._history: list[Alert] = []
        self._loaded = False
        # R-monkey 审查：_history 是「读-改-写」可变状态，R2-M-05 只为落盘加了
        # 原子写但内存侧仍无锁——多线程 record/clear_history 交错时 extend 与
        # 截断可互相覆盖（丢更新），join 后「磁盘==内存」的 oracle 只是概率成立。
        # 生产路径（UI 线程单写）成本可忽略，加锁与 HistoryStore 的 R2-M-05 同治。
        self._history_lock = threading.Lock()

    def reset_state(self) -> None:
        """清空去重状态，让下一次判定从零开始。

        换品种、改阈值或清空提醒历史后调用，用户不必重启程序就能重新收到提醒。
        """
        self._pct_seen = None
        self._pct_at = None
        self._extreme_water.clear()
        self._target_state.clear()
        self._day = ""

    # ------------------------------------------------------------ 去重辅助
    def _cooldown(self) -> int:
        """防抖间隔（秒）。手改配置成负数也能兜住。"""
        return max(0, int(self.cfg.get("alert_cooldown_sec", 600)))

    def _roll_day(self, now: float) -> None:
        """跨日时清空当日水位：新的一天重新计「今日新高」。"""
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if day != self._day:
            self._day = day
            self._extreme_water.clear()

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
        self._roll_day(now)
        alerts: list[Alert] = []
        alerts += self._check_pct(quote, metal_name, now)
        alerts += self._check_extreme(quote, metal_name, now)
        alerts += self._check_target(base_quote or quote, metal_name, now)
        return alerts

    def _check_pct(self, quote: Quote, metal_name: str, now: float) -> list[Alert]:
        """涨跌幅提醒：按阈值分档，只在跨入更高档位时提醒。

        阈值 0.5% 时档位为 0.5~1.0 → 1 档、1.0~1.5 → 2 档…… 横盘在 1.14% 只会提醒
        一次（2 档），不会再随时间反复弹窗。
        """
        if not self._enabled(KIND_PCT):
            return []
        threshold = float(self.cfg.get("alert_pct", 0.5))
        magnitude = abs(quote.change_pct)

        if threshold <= 0 or magnitude < threshold:
            # 回到阈值以内：重新武装，下次再越线可以提醒（受防抖约束）
            self._pct_seen = None
            return []

        side = "up" if quote.change_pct > 0 else "down"
        step = int(magnitude / threshold)  # 阈值整数倍：1 档 / 2 档 / 3 档 …
        seen = self._pct_seen

        if seen is not None and seen[0] == side and step <= seen[1]:
            # 同方向且档位没有更高 —— 内容与上次一模一样，不再提醒
            return []
        if seen is None and self._pct_at is not None and now - self._pct_at < self._cooldown():
            # 刚从阈值内回来又立刻越线：视为抖动，交给防抖过滤
            return []

        self._pct_seen = (side, step)
        self._pct_at = now
        arrow = "上涨" if quote.change_pct > 0 else "下跌"
        return [
            Alert(
                kind=KIND_PCT,
                side=side,
                title="黄金桌宠提醒",
                body=f"{metal_name}价{arrow} {magnitude:.2f}%，已超过设定阈值 {threshold:.2f}%",
                at=now,
            )
        ]

    def _check_extreme(self, quote: Quote, metal_name: str, now: float) -> list[Alert]:
        """新高新低提醒：只有严格刷新当日水位、且幅度达到最小步进才算数。

        行情源的当日最高价本身就会被现价不断顶上去，因此不能直接用「现价 == 当日高」
        判定（那样横盘时会反复命中）。这里额外记住「上次播报过的价位」，要求现价严格
        越过它才提醒，水位单调因此天然不重复。

        再加一道「最小步进」（alert_extreme_step_pct，默认 0.1%）：金价在 15:21~15:23
        连续刷出 907.70 / 907.96 / 908.07 时，按纯粹的水位判定会连弹三次，而 0.1%
        的步进会把它们收敛成一次。设 0 即退化为「任何新高都提醒」。
        """
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

        prev = self._extreme_water.get(side)
        if prev is not None:
            ratio = max(0.0, float(self.cfg.get("alert_extreme_step_pct", 0.1))) / 100.0
            if side == "high" and quote.price <= prev * (1 + ratio):
                return []  # 水位只多了一点点：不值得再提醒
            if side == "low" and quote.price >= prev * (1 - ratio):
                return []

        self._extreme_water[side] = quote.price
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
        """到价提醒：以元/克为基准，与当前展示单位无关。

        只在「从目标价外进入目标价内」的那一刻提醒一次；价格回到目标价外后重新武装，
        因此横盘在目标价之上不会反复弹窗。
        """
        if not self._enabled(KIND_TARGET):
            return []
        high = float(self.cfg.get("target_high", 0.0))
        low = float(self.cfg.get("target_low", 0.0))
        if high <= 0 and low <= 0:
            return []
        if not base.unit.startswith("元"):
            return []  # 拿不到元/克基准就不判断，避免单位错配

        # 先处理「退出生效态」：回到目标价外就重新武装
        if high > 0 and base.price < high:
            self._target_state["high"] = False
        if low > 0 and base.price > low:
            self._target_state["low"] = False

        if high > 0 and base.price >= high:
            side, body = "high", f"金价已涨到 {base.price:.2f} 元/克（目标 {high:.2f}）"
        elif low > 0 and base.price <= low:
            side, body = "low", f"金价已跌到 {base.price:.2f} 元/克（目标 {low:.2f}）"
        else:
            return []

        if self._target_state.get(side):
            return []  # 已在生效态：不重复提醒
        self._target_state[side] = True
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
        """把提醒追加到历史（内存 + 落盘）。

        R-monkey 审查：读-改-写（extend/截断/_save）全程持锁，多线程 record
        不再互相覆盖；与 HistoryStore.append（R2-M-05）同治。
        """
        if not alerts:
            return
        with self._history_lock:
            self._ensure_loaded()
            self._history.extend(alerts)
            if len(self._history) > self.max_history:
                self._history = self._history[-self.max_history :]
            self._save()

    def history(self, kind: str | None = None) -> list[Alert]:
        """取提醒历史（按时间倒序）。kind 为 None 时返回全部。"""
        with self._history_lock:
            self._ensure_loaded()
            items = [a for a in self._history if kind is None or a.kind == kind]
        return sorted(items, key=lambda a: a.at, reverse=True)

    def clear_history(self) -> None:
        with self._history_lock:
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
        except (OSError, ValueError, RecursionError):
            # RecursionError：R2-M-03，超深嵌套的 alert_history.json 不让炸穿加载链路
            return
        if not isinstance(raw, list):
            return
        # P2-03：逐项容错——坏记录跳过并记日志，不让一次手改坏文件中断提醒链路
        history: list[Alert] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                history.append(Alert.from_dict(item))
            except (TypeError, ValueError):
                logger.warning("alert history: 跳过 1 条无法解析的坏记录", exc_info=True)
        self._history = history[-self.max_history :]

    def _save(self) -> None:
        if self.history_path is None:
            return
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            # R2-M-05：原子写，CLI 并发读 alert_history.json 不会看到半截 JSON
            _atomic_write_text(
                self.history_path,
                json.dumps([a.to_dict() for a in self._history], ensure_ascii=False, indent=1),
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
    P2-03：t/p 非数值的坏点跳过并记日志（缺字段仍按 0 处理），不让脏数据
    在刷新渲染链路抛 ValueError。
    """
    cutoff = spark_cutoff(range_key, now)
    values: list[float] = []
    skipped = 0
    for point in series:
        if not isinstance(point, dict):
            skipped += 1
            continue
        price = _safe_float(point.get("p", 0.0))
        if price is None:
            skipped += 1
            continue
        if cutoff is not None:
            ts = _safe_float(point.get("t", 0.0))
            if ts is None:  # 有时间过滤却给不出时间：坏点
                skipped += 1
                continue
            if ts < cutoff:
                continue  # 正常的旧数据点，不算坏
        values.append(price)
    if skipped:
        logger.warning("spark: 序列含 %d 条坏数据点，已跳过", skipped)
    return values
