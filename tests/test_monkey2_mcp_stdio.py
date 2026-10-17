"""Monkey 测试 R2 目标 6：MCP stdio 桥（tools/pet_mcp_server.py）深度 fuzz——一轮空白面。

种子：20261016。
覆盖：initialize 握手后灌垃圾行（语法错/非法 UTF-8/10MB 单行/2 万层嵌套 params/
畸形 id 类型/合法工具的错误参数类型），每个场景后用 ping 探测会话活性；
5 个并发桥进程同时握手+调用。

已知缺陷（R2-M-07，第三方 mcp 1.30 库域，xfail 固化）：
未知方法 / tools/call 烂 name 会把会话打死（进程存活但永不应答）。

oracle：进程存活、合法请求 ≤6s 内得到 JSON 响应（握手 8s）。
"""

from __future__ import annotations

import importlib.util
import json
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SEED = 20261016
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER = PROJECT_ROOT / "tools" / "pet_mcp_server.py"
INIT = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "monkey2", "version": "0"},
    },
}

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None, reason="当前解释器未安装 mcp 包"
)


class BridgeSession:
    """一个 MCP 桥子进程 + stdout 行泵。"""

    def __init__(self):
        self.p = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.lines: list[str] = []
        self._t = threading.Thread(target=self._pump, daemon=True)
        self._t.start()

    def _pump(self) -> None:
        try:
            for ln in self.p.stdout:
                self.lines.append(ln.decode("utf-8", errors="replace"))
        except Exception:
            pass

    def send_raw(self, raw: bytes) -> bool:
        try:
            self.p.stdin.write(raw)
            self.p.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            return False

    def send(self, obj) -> bool:
        return self.send_raw((json.dumps(obj) + "\n").encode("utf-8"))

    def wait_reply(self, req_id, timeout: float = 6.0):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            for ln in self.lines:
                try:
                    msg = json.loads(ln)
                except ValueError:
                    continue
                if (
                    isinstance(msg, dict)
                    and msg.get("id") == req_id
                    and ("result" in msg or "error" in msg)
                ):
                    return msg
            time.sleep(0.02)
        return None

    def handshake(self) -> bool:
        if not self.send(INIT) or self.wait_reply(0, timeout=8.0) is None:
            return False
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return True

    def ping_ok(self, timeout: float = 6.0) -> bool:
        self.send({"jsonrpc": "2.0", "id": 424242, "method": "ping"})
        return self.wait_reply(424242, timeout=timeout) is not None

    def close(self) -> None:
        try:
            self.p.kill()
        except OSError:
            pass


def _scenario(garbage: list[bytes], expect_alive: bool) -> None:
    s = BridgeSession()
    try:
        assert s.handshake(), "initialize 握手失败"
        for g in garbage:
            s.send_raw(g)
        time.sleep(0.5)
        alive = s.ping_ok()
        assert alive is expect_alive, (
            f"垃圾灌入后会话活性 {alive}（预期 {expect_alive}），进程状态 {s.p.poll()}"
        )
        assert s.p.poll() is None, "桥进程不应退出"
    finally:
        s.close()


def test_r2_mcp_survives_syntax_garbage():
    rnd = random.Random(SEED)
    garbage = [
        b"hello\n",
        b"{\n",
        b"[1,2,3]\n",
        b'{"jsonrpc": "2.0"}\n',
        b'{"jsonrpc":"2.0","id":' + str(rnd.randint(1, 99)).encode() + b',"method":}\n',
    ]
    _scenario(garbage, expect_alive=True)


def test_r2_mcp_survives_invalid_utf8():
    _scenario(
        [b"\xff\xfe\x00bad-utf8\n", b"\x80\x81\x82\n", b'{"a": "\xc3\x28"}\n'], expect_alive=True
    )


def test_r2_mcp_survives_10mb_line():
    _scenario(
        [
            b'{"jsonrpc":"2.0","method":"ping","id":1,"params":"'
            + b"A" * (10 * 1024 * 1024)
            + b'"}\n'
        ],
        expect_alive=True,
    )


def test_r2_mcp_survives_deep_params():
    deep = (
        b'{"jsonrpc":"2.0","id":1,"method":"ping","params":' + b"[" * 20000 + b"]" * 20000 + b"}\n"
    )
    _scenario([deep], expect_alive=True)


def test_r2_mcp_survives_tool_wrong_arg_types():
    """合法工具 + 错误参数类型：pet_say text=123 / pet_move dx=1e400 —— 不得影响会话。"""
    _scenario(
        [
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"pet_say","arguments":{"text":123}}}\n',
            b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"pet_move","arguments":{"dx":1e400}}}\n',
            b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"pet_play","arguments":{"state":null}}}\n',
        ],
        expect_alive=True,
    )


@pytest.mark.xfail(
    reason=(
        "R2-M-07（P2，遗留）：mcp 1.30 库对畸形入站请求的响应任务会（非确定性）被打死——"
        "未知方法、tools/call 烂 name/params、畸形 id 类型（1.5/obj/list/bool）单独或组合"
        "灌入后，进程存活但此后对合法请求永不应答（stderr 为 pydantic literal_error 噪声）。"
        "同一条消息单独发有时可存活，组合发稳定复现。第三方库域，项目侧无干净的桥内缓解；"
        "建议升级 mcp 或宿主侧超时重启。"
    ),
    strict=False,
)
def test_r2_mcp_malformed_requests_may_wedge_session():
    _scenario(
        [
            b'{"jsonrpc":"2.0","id":1,"method":"no/such"}\n',
            b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":null}\n',
            b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":123}}\n',
            b'{"jsonrpc":"2.0","method":"ping","id":1.5}\n',
            b'{"jsonrpc":"2.0","method":"ping","id":{"a":1}}\n',
            b'{"jsonrpc":"2.0","method":"ping","id":[1]}\n',
            b'{"jsonrpc":"2.0","method":"ping","id":true}\n',
            b'{"jsonrpc":"2.0","id":4}\n',
        ],
        expect_alive=True,
    )


def test_r2_mcp_five_concurrent_bridges():
    """5 个并发桥进程同时握手 + tools/list + ping：全部应答。"""
    results: list[str] = []
    lock = threading.Lock()

    def run(idx: int) -> None:
        s = BridgeSession()
        try:
            if not s.handshake():
                with lock:
                    results.append(f"p{idx}: 握手失败")
                return
            s.send({"jsonrpc": "2.0", "id": idx, "method": "tools/list"})
            if s.wait_reply(idx, timeout=10.0) is None:
                with lock:
                    results.append(f"p{idx}: tools/list 无响应")
                return
            if not s.ping_ok():
                with lock:
                    results.append(f"p{idx}: ping 无响应")
            else:
                with lock:
                    results.append(f"p{idx}: ok")
        finally:
            s.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r.endswith("ok") for r in results), results
