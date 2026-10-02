r"""AI 控制服务（v0.8）：本地 HTTP + token 鉴权，供 AI 操控鲸娘。

安全模型（与 AutoClicker-Pro v1.0.52 的 AI 控制服务同款）：
- 只绑定 127.0.0.1 + 随机高位端口，外部机器物理上无法连接
- 每次启动用 secrets.token_urlsafe 生成随机 token，请求头 X-GP-Token 必须匹配
- 端口与 token 写入发现文件 control.json（%APPDATA%\GoldPet\，仅本机当前用户可读），
  MCP 桥接进程（tools/pet_mcp_server.py）读该文件自动发现接入信息
- 应用退出时删除发现文件并关停 HTTP 线程

线程模型：HTTP 跑在 QThread 里，收到动作后经 Qt Signal 转发到 UI 线程执行；
HTTP 线程绝不直接触碰任何 QWidget（Qt 界面对象只允许主线程操作）。

路由一览（全部 JSON）：
- GET  /status  -> 应用版本、当前状态、活跃度、大肥鱼计数
- POST /say     -> {"text": "..."} 自由发言 或 {"mood": "up"} 从台词组随机挑一句
- POST /play    -> {"state": "pat"} 播放一段动作动画（白名单内）
- POST /pat     -> 摸摸头（哄好大肥鱼傲娇）
- POST /tease   -> 叫她大肥鱼（触发傲娇彩蛋）
- POST /move    -> {"dx": n, "dy": n} 相对挪动 或 {"x": n, "y": n} 移动到绝对坐标
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import secrets
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from PySide6.QtCore import QThread, Signal

from . import __version__
from .paths import control_info_path

logger = logging.getLogger("gold_pet")

# /play 允许播放的状态白名单：只开放「表演型」动作，
# loading/offline 是行情驱动的状态，不允许外部乱设。
PLAYABLE_STATES = ("idle", "up", "down", "pat", "shy")

# 请求体大小上限：控制指令都是小 JSON，64KB 绰绰有余（防误用/恶意大包）
_MAX_BODY_BYTES = 64 * 1024

# /say 自由发言的文本上限（P2-01）：超长文本会把气泡撑爆/变形，超出部分截断。
# HTML 转义统一在渲染边界 bubble.render_line_html 做，这里只管限长。
_MAX_SAY_CHARS = 500


def _make_handler():
    """返回 Handler 类。写成工厂是为了避免模块级单例状态（测试可反复构建）。"""

    class PetRequestHandler(BaseHTTPRequestHandler):
        """单请求处理器：鉴权 -> 解析 -> 分发动作 -> 回 JSON。"""

        server: PetHTTPServer  # 类型提示：挂在自定义 server 上

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: N802
            # 默认实现往 stderr 打，重定向到项目日志，避免污染控制台
            logger.debug("control: %s", fmt % args)

        # ------------------------------------------------------------ 响应工具
        def _send_json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _check_auth(self) -> bool:
            if self.headers.get("X-GP-Token", "") == self.server.token:
                return True
            self._send_json(401, {"ok": False, "error": "token 无效或缺失（X-GP-Token）"})
            return False

        def _read_json(self) -> dict | None:
            """读请求体并解析 JSON；失败时自己回 400 并返回 None。"""
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0:
                return {}
            if length > _MAX_BODY_BYTES:
                self._send_json(413, {"ok": False, "error": "请求体过大"})
                return None
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json(400, {"ok": False, "error": "请求体不是合法 JSON"})
                return None
            if not isinstance(data, dict):
                self._send_json(400, {"ok": False, "error": "请求体必须是 JSON 对象"})
                return None
            return data

        # ------------------------------------------------------------ 路由
        def do_GET(self) -> None:  # noqa: N802
            if not self._check_auth():
                return
            if self.path.split("?", 1)[0] == "/status":
                self._send_json(200, {"ok": True, **self.server.state_reader()})
                return
            self._send_json(404, {"ok": False, "error": "未知路径"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._check_auth():
                return
            path = self.path.split("?", 1)[0]
            if path not in ("/say", "/play", "/pat", "/tease", "/move"):
                self._send_json(404, {"ok": False, "error": "未知路径"})
                return
            data = self._read_json()
            if data is None:  # 已回 400/413
                return

            if path == "/say":
                text = data.get("text")
                mood = data.get("mood")
                if isinstance(text, str) and text.strip():
                    # P2-01：限长，防超长文本撑爆气泡（HTML 转义在渲染层统一做）
                    self.server.dispatch(
                        {"action": "say", "text": text.strip()[:_MAX_SAY_CHARS]}
                    )
                    self._send_json(200, {"ok": True, "mode": "text"})
                    return
                if isinstance(mood, str) and mood.strip():
                    self.server.dispatch({"action": "say", "mood": mood.strip()})
                    self._send_json(200, {"ok": True, "mode": "mood"})
                    return
                self._send_json(400, {"ok": False, "error": "需要 text（自由发言）或 mood（台词组）"})
                return

            if path == "/play":
                state = data.get("state")
                if state not in PLAYABLE_STATES:
                    self._send_json(
                        400,
                        {"ok": False, "error": f"state 必须是 {'/'.join(PLAYABLE_STATES)} 之一"},
                    )
                    return
                self.server.dispatch({"action": "play", "state": state})
                self._send_json(200, {"ok": True})
                return

            if path == "/move":
                dx, dy = data.get("dx"), data.get("dy")
                x, y = data.get("x"), data.get("y")
                has_rel = isinstance(dx, (int, float)) and isinstance(dy, (int, float))
                has_abs = isinstance(x, (int, float)) and isinstance(y, (int, float))
                if not (has_rel or has_abs):
                    self._send_json(400, {"ok": False, "error": "需要 dx/dy（相对挪动）或 x/y（绝对坐标）"})
                    return
                action: dict[str, Any] = {"action": "move"}
                if has_rel:
                    action["dx"], action["dy"] = int(dx), int(dy)  # type: ignore[arg-type]
                else:
                    action["x"], action["y"] = int(x), int(y)  # type: ignore[arg-type]
                self.server.dispatch(action)
                self._send_json(200, {"ok": True})
                return

            # /pat /tease：无参数动作
            name = path.lstrip("/")  # pat | tease
            self.server.dispatch({"action": name})
            self._send_json(200, {"ok": True})

    return PetRequestHandler


class PetHTTPServer(ThreadingHTTPServer):
    """挂上桌宠上下文的 HTTP 服务（鉴权 token / 状态读取 / 动作分发）。"""

    daemon_threads = True  # 退出时不等连接收尾，避免卡住关机流程
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        token: str,
        state_reader: Callable[[], dict],
    ):
        super().__init__(address, _make_handler())
        self.token = token
        self.state_reader = state_reader
        # 动作出口：由 ControlServer 注入 signal.emit，测试可注入收集器
        self.on_action: Callable[[dict], None] = lambda action: None

    def dispatch(self, action: dict) -> None:
        self.on_action(action)


def build_server(
    address: tuple[str, int],
    token: str,
    state_reader: Callable[[], dict],
) -> PetHTTPServer:
    """构建 HTTP 服务实例（独立于 QThread，方便纯协议层测试）。"""
    return PetHTTPServer(address, token, state_reader)


class ControlServer(QThread):
    """桌宠内置控制服务：管理 HTTP 线程生命周期与发现文件。

    用法（UI 主线程）：
        self.control = ControlServer(state_reader=self._control_state)
        self.control.action_received.connect(self._handle_control)
        self.control.start()
    退出时：
        self.control.stop()   # 停 HTTP + 删发现文件
    """

    # 动作经此信号跨线程转发：HTTP 线程 emit，UI 线程槽里执行
    action_received = Signal(dict)

    def __init__(self, state_reader: Callable[[], dict], parent=None):
        super().__init__(parent)
        self._state_reader = state_reader
        self._server: PetHTTPServer | None = None
        self.port: int = 0
        self.token: str = ""
        self.started_ok = False

    # ------------------------------------------------------------------ QThread
    def run(self) -> None:
        # 清掉上次异常退出残留的发现文件（端口/token 都已失效）
        try:
            control_info_path().unlink(missing_ok=True)
        except OSError:
            logger.warning("control: 旧发现文件删除失败", exc_info=True)

        token = secrets.token_urlsafe(24)
        try:
            server = build_server(("127.0.0.1", 0), token, self._state_reader)
        except OSError:
            # 端口绑定失败极罕见（随机端口 + 仅回环），放弃控制服务但不影响桌宠本体
            logger.error("control: 本地端口绑定失败，本次启动不提供 AI 控制服务")
            return

        server.on_action = self.action_received.emit
        self._server = server
        self.port = server.server_address[1]
        self.token = token
        self.started_ok = True
        self._write_info()
        logger.info("control: AI 控制服务已启动 127.0.0.1:%d", self.port)
        server.serve_forever(poll_interval=0.2)
        logger.info("control: AI 控制服务已退出")

    def stop(self) -> None:
        """停服务 + 删发现文件（从主线程调用，serve_forever 会在 shutdown 后返回）。"""
        server = self._server
        if server is not None:
            self._server = None
            server.shutdown()
            server.server_close()
        self._remove_info()

    # ------------------------------------------------------------------ 发现文件
    def _write_info(self) -> None:
        info = {
            "port": self.port,
            "token": self.token,
            "pid": os.getpid(),
            "version": __version__,
        }
        path = control_info_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _remove_info() -> None:
        try:
            control_info_path().unlink(missing_ok=True)
        except OSError:
            logger.warning("control: 发现文件删除失败", exc_info=True)


def _pid_alive(pid: int) -> bool:
    """跨平台进程探活。

    注意：Windows 上 os.kill(pid, 0) 的语义是 TerminateProcess（直接杀进程！），
    绝不能用；必须走 OpenProcess + GetExitCodeProcess 探活。
    """
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:  # 打不开 = 不存在或无权限（同用户的残留进程能打开）
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # POSIX：signal 0 只探活不发送
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但无权限发信号
    return True


def read_control_info() -> dict | None:
    """读取发现文件（MCP 桥接进程用）。文件不存在或 pid 已死都返回 None。"""
    path = control_info_path()
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or not info.get("port") or not info.get("token"):
        return None
    # 进程已退出的残留文件：桥接进程别拿它去连
    pid = info.get("pid")
    if isinstance(pid, int) and not _pid_alive(pid):
        return None
    return info
