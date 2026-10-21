"""关怀提醒（喝水 / 久坐 / 护眼）—— 陪伴类主动台词的调度。

产品语义（v0.7 与用户确认）：
  - 只通过鲸娘气泡触达，**不弹系统通知、不进提醒中心**（与行情警报区分开）
  - 喝水提醒默认开启，久坐 / 护眼默认关闭
  - 遵守勿扰时段：勿扰内不出声（由调用方判断后传入）
  - 计时从程序启动开始，重启重新计时；设置变更时由调用方调用 reset()

设计原则与 notifier.py 一致：纯逻辑 + 可注入时钟，方便单测。
"""

from __future__ import annotations

import math
import time

# kind -> (开关配置键, 间隔配置键, 默认间隔分钟)
CARE_KINDS: dict[str, tuple[str, str, int]] = {
    "care_drink": ("care_drink_enabled", "care_drink_interval_min", 60),
    "care_posture": ("care_posture_enabled", "care_posture_interval_min", 90),
    "care_eye": ("care_eye_enabled", "care_eye_interval_min", 45),
}

# 多个提醒同时到期时的说话顺序（喝水最要紧，其余顺延）
_PRIORITY = list(CARE_KINDS)

# 同时到期被顺延时，推迟多少秒再问一次（避免连说两三条）
_DEFER_SEC = 90.0


def _now() -> float:
    """计时用的时间源：单调时钟，系统改时间/校时都不影响。"""
    return time.monotonic()


def _as_ts(value: object, fallback: float = 0.0) -> float:
    """把外部传入的时间点规整成有限浮点，非法值（NaN/None/字符串）退回 fallback。"""
    try:
        ts = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return ts if math.isfinite(ts) else fallback


class CareScheduler:
    """到点提醒调度：每分钟 tick 一次，返回该说话的 kind（或 None）。

    时间约定：now 一律是「单调时钟秒数」（time.monotonic()），
    调用方与调度器必须用同一个时间源；不传则取当前值。
    此前默认 now=0.0：首启排期被算成 monotonic 起点 +1 小时，
    启动半分钟后的第一次 tick 就「到点」，于是刚开程序就冒喝水提醒。
    """

    def __init__(self, cfg, now: float | None = None):
        self.cfg = cfg
        self._next: dict[str, float] = {}
        # 传了时间就按传入的排（纯逻辑可注入），没传就取当前时钟
        self.reset(self.now() if now is None else _as_ts(now))

    @staticmethod
    def now() -> float:
        """调度器使用的当前时间（单调时钟秒数），供调用方对齐。"""
        return _now()

    # ---------------------------------------------------------------- 对外
    def reset(self, now: float | None = None) -> None:
        """按当前配置重排所有提醒（启动 / 设置变更时调用）。"""
        now = self.now() if now is None else _as_ts(now)
        self._next.clear()
        for kind in CARE_KINDS:
            if self._enabled(kind):
                self._next[kind] = now + self._interval_sec(kind)

    def due(self, now: float, dnd_active: bool = False) -> str | None:
        """返回该说话的 kind；勿扰期内不说话但保留计时（出来后立即补上）。"""
        if dnd_active:
            return None
        for kind in _PRIORITY:
            at = self._next.get(kind)
            if at is not None and now >= at:
                return kind
        return None

    def mark(self, kind: str, now: float) -> None:
        """说完一句话后排下一次。"""
        self._next[kind] = _as_ts(now, self.now()) + self._interval_sec(kind)

    def defer(self, kind: str, now: float) -> None:
        """同一时刻到期了多条时，把没轮到的往后推一小段，避免连发。"""
        now = _as_ts(now, self.now())
        at = self._next.get(kind)
        if at is not None and now >= at:
            self._next[kind] = now + _DEFER_SEC

    def resync(self, now: float | None = None) -> bool:
        """时钟跳变兜底：now 早于任何一个周期起点（时钟被往回调过）时整体重排。

        返回是否发生了重排。调度器走的是单调时钟，正常不会触发；
        这里防的是调用方喂了非单调时间源（如 time.time()）或跨进程续跑。
        """
        now = self.now() if now is None else _as_ts(now)
        if not self._next:
            return False
        earliest = min(at - self._interval_sec(kind) for kind, at in self._next.items())
        if now >= earliest:
            return False
        self.reset(now)
        return True

    # ---------------------------------------------------------------- 内部
    def _enabled(self, kind: str) -> bool:
        enabled_key, _, _ = CARE_KINDS[kind]
        return bool(self.cfg.get(enabled_key, True))

    def _interval_sec(self, kind: str) -> float:
        _, interval_key, default_min = CARE_KINDS[kind]
        return int(self.cfg.get(interval_key, default_min)) * 60
