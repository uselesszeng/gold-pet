"""控制协议常量（REVIEW5-04）：CLI 与桌宠服务端的**唯一事实来源**。

为什么单独一个模块：这些常量原先在 `cli.py` 与 `gold_pet/control_server.py`
各写一份，靠一句注释「服务端改动时必须同步这两处」约束——**注释约等于没有约束**，
漏同步就静默产生两端语义不一致（CLI 放行 600 字而服务端截断到 500；CLI 放行某
动作而服务端回 400）。集成方（AI 宿主 / MCP 桥 / 脚本）看到的两端行为会不一样。

为什么不直接让 cli 从 control_server 导入：`control_server.py` 顶层
`from PySide6.QtCore import QThread, Signal`，而 CLI 的设计前提是**无 GUI 依赖**
（可在纯命令行/CI 环境跑）。把常量下沉到本模块后，两侧都是「引用同一对象」，
既消除重复又保住 CLI 的零 GUI 依赖。

注意：本模块只放**协议层常量**（跨越 HTTP 边界的取值约定），不放实现细节。
"""

from __future__ import annotations

# /play 允许播放的状态白名单：只开放「表演型」动作，
# loading/offline 是行情驱动的状态，不允许外部乱设。
PLAYABLE_STATES = ("idle", "up", "down", "pat", "shy")

# /say 自由发言的文本上限（P2-01）：超长文本会把气泡撑爆/变形，超出部分截断。
# HTML 转义统一在渲染边界 bubble.render_line_html 做，这里只管限长。
MAX_SAY_CHARS = 500

# 请求体大小上限：控制指令都是小 JSON，64KB 绰绰有余（防误用/恶意大包）
MAX_BODY_BYTES = 64 * 1024

# 三类提醒的类型标识（与 notifier.KIND_* 同源，供 CLI 校验 --kind 用）
ALERT_KINDS = ("pct", "target", "extreme")
