"""关怀提醒（喝水 / 久坐 / 护眼）—— 陪伴类主动台词的调度。

产品语义（v0.7 与用户确认）：
  - 只通过鲸娘气泡触达，**不弹系统通知、不进提醒中心**（与行情警报区分开）
  - 喝水提醒默认开启，久坐 / 护眼默认关闭
  - 遵守勿扰时段：勿扰内不出声（由调用方判断后传入）
  - 计时从程序启动开始，重启重新计时；设置变更时由调用方调用 reset()

设计原则与 notifier.py 一致：纯逻辑 + 可注入时钟，方便单测。
"""

from __future__ import annotations

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


class CareScheduler:
    """到点提醒调度：每分钟 tick 一次，返回该说话的 kind（或 None）。"""

    def __init__(self, cfg, now: float = 0.0):
        self.cfg = cfg
        self._next: dict[str, float] = {}
        self.reset(now)

    # ---------------------------------------------------------------- 对外
    def reset(self, now: float) -> None:
        """按当前配置重排所有提醒（启动 / 设置变更时调用）。"""
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
        self._next[kind] = now + self._interval_sec(kind)

    def defer(self, kind: str, now: float) -> None:
        """同一时刻到期了多条时，把没轮到的往后推一小段，避免连发。"""
        at = self._next.get(kind)
        if at is not None and now >= at:
            self._next[kind] = now + _DEFER_SEC

    # ---------------------------------------------------------------- 内部
    def _enabled(self, kind: str) -> bool:
        enabled_key, _, _ = CARE_KINDS[kind]
        return bool(self.cfg.get(enabled_key, True))

    def _interval_sec(self, kind: str) -> float:
        _, interval_key, default_min = CARE_KINDS[kind]
        return int(self.cfg.get(interval_key, default_min)) * 60
