r"""黄金桌宠 MCP 桥接服务（v0.8）：让 AI 通过 MCP 工具操控鲸娘。

原理：
    AI 宿主（WorkBuddy / Cursor）--stdio--> 本桥接进程 --HTTP(127.0.0.1+token)--> 桌宠控制服务

- 桥接进程是「无状态」的：每次工具调用都重新读取桌宠写的发现文件
  control.json（%APPDATA%\GoldPet\），桌宠重启换端口/换 token 也不需要改配置。
- 安全：HTTP 只到 127.0.0.1，token 由桌宠每次启动随机生成，本桥接进程
  从本机用户目录读取，全程不出本机。

注册到 AI 宿主（以 WorkBuddy / Cursor 的 mcp.json 为例）：
    {
      "mcpServers": {
        "gold-pet": {
          "command": "<gold-pet 项目 venv 的 python.exe>",
          "args": ["<gold-pet 项目>/tools/pet_mcp_server.py"]
        }
      }
    }
打包版用户可把 command 换成系统 Python（需 pip install mcp httpx）或项目自带
启动器；桥接进程只依赖 mcp 包，不依赖 PySide6。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

# 桥接进程独立运行，不能假设 gold_pet 包可导入——自带一份发现文件路径逻辑
_APP_NAME = "GoldPet"

if sys.platform == "win32":
    import os

    _INFO_PATH = Path(os.environ.get("APPDATA", Path.home())) / _APP_NAME / "control.json"
else:
    _INFO_PATH = Path.home() / f".{_APP_NAME.lower()}" / "control.json"

mcp = FastMCP("gold-pet")


# ------------------------------------------------------------------ 底层调用
def _discover() -> tuple[int, str]:
    """读桌宠的发现文件 -> (port, token)；桌宠没在运行时抛友好异常。"""
    try:
        info = json.loads(_INFO_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError(
            "桌宠没有在运行（未找到接入信息 control.json）。"
            "请先启动黄金桌宠，再调用本工具。"
        ) from None
    except ValueError:
        raise RuntimeError("接入信息文件损坏，请重启黄金桌宠。") from None
    port, token = info.get("port"), info.get("token")
    if not port or not token:
        raise RuntimeError("接入信息不完整，请重启黄金桌宠。")
    return int(port), str(token)


def _call(method: str, path: str, payload: dict | None = None, success: str = "完成") -> str:
    """调用桌宠控制服务并返回人类可读的结果文本。"""
    port, token = _discover()
    url = f"http://127.0.0.1:{port}{path}"
    headers = {"X-GP-Token": token}
    try:
        resp = httpx.request(method, url, json=payload or {}, headers=headers, timeout=5.0)
    except httpx.HTTPError:
        return "调用失败：桌宠没有响应，可能刚退出。请确认黄金桌宠在运行后重试。"
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code == 401:
        return "调用失败：token 不匹配（桌宠可能重启过），请重试一次。"
    if not data.get("ok"):
        return f"调用失败：{data.get('error', f'HTTP {resp.status_code}')}"
    return data.get("message") or success


# ------------------------------------------------------------------ MCP 工具
@mcp.tool()
def pet_status() -> str:
    """查看鲸娘当前状态：版本、动画状态、活跃度、被叫「大肥鱼」的次数。"""
    port, token = _discover()
    try:
        resp = httpx.get(
            f"http://127.0.0.1:{port}/status",
            headers={"X-GP-Token": token},
            timeout=5.0,
        )
    except httpx.HTTPError:
        return "桌宠没有响应，可能刚退出。请确认黄金桌宠在运行后重试。"
    try:
        data = resp.json()
    except ValueError:
        return "桌宠返回了无法解析的内容。"
    if not data.get("ok"):
        return f"调用失败：{data.get('error', f'HTTP {resp.status_code}')}"
    return (
        f"鲸娘状态：{data.get('state', '?')}｜活跃度：{data.get('activity_level', '?')}｜"
        f"被叫大肥鱼：{data.get('fatty_count', 0)} 次｜桌宠版本：v{data.get('version', '?')}"
    )


@mcp.tool()
def pet_say(text: str = "", mood: str = "") -> str:
    """让鲸娘说话。

    两种方式二选一：
    - text：自由发挥的一句话（AI 可以根据上下文创作，走鲸娘口吻更佳）
    - mood：从内置台词组随机挑一句，可选值：
        up（涨）/ down（跌）/ idle（日常）/ pat（被摸头）/ shy（害羞）/
        welcome（欢迎）/ loading（加载中）/ offline（断网）/
        act_quiet / act_normal / act_lively（活跃度切换感想）
    """
    if text.strip():
        return _call("POST", "/say", {"text": text}, success="鲸娘说完了～")
    if mood.strip():
        return _call("POST", "/say", {"mood": mood}, success="鲸娘说完了～")
    return "需要 text（自由发挥）或 mood（台词组）之一。"


@mcp.tool()
def pet_play(state: str) -> str:
    """让鲸娘播放一段动作动画。

    state 可选值：idle（浮游）/ up（开心上浮）/ down（低落下沉）/
    pat（被摸头眯眼）/ shy（害羞扭捏）。
    """
    return _call("POST", "/play", {"state": state}, success="鲸娘做完了动作～")


@mcp.tool()
def pet_pat() -> str:
    """摸摸鲸娘的头：她会眯眼笑，还会哄好被叫「大肥鱼」的傲娇（生气计数归零）。"""
    return _call("POST", "/pat", success="鲸娘被摸头了，眯眼笑～傲娇值已清零")


@mcp.tool()
def pet_tease() -> str:
    """叫鲸娘「大肥鱼」：她会傲娇反驳，连续叫会越来越生气（摸头可哄好）。慎用～"""
    return _call("POST", "/tease", success="你又叫她大肥鱼了，鲸娘很生气！")


@mcp.tool()
def pet_move(dx: int = 0, dy: int = 0, x: int = -1, y: int = -1) -> str:
    """挪动鲸娘在屏幕上的位置（会自动夹取回屏幕内）。

    - 相对挪动：给 dx / dy（像素偏移，可为负）
    - 绝对定位：给 x / y（屏幕左上角为原点）
    两者都给时优先相对挪动。
    """
    if dx or dy:
        return _call("POST", "/move", {"dx": int(dx), "dy": int(dy)}, success="鲸娘挪好位置啦～")
    if x >= 0 and y >= 0:
        return _call("POST", "/move", {"x": int(x), "y": int(y)}, success="鲸娘挪好位置啦～")
    return "需要 dx/dy（相对挪动）或 x/y（绝对坐标）。"


if __name__ == "__main__":
    mcp.run()  # stdio transport（WorkBuddy / Cursor 默认）
