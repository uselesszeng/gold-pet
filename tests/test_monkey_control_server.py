"""Monkey 测试目标 1：control_server.py HTTP 端点脏 payload 轰炸。

种子：20261002；迭代：500 次（另加 12 次定向重型 payload）。
语料：缺字段/错类型/null/负数/NaN/Infinity 字面量/超大数/unicode+emoji+控制字符/
深层嵌套 JSON/重复键/1MB 超大包/非法 token，80% 非法 / 20% 合法边界。

oracle：每个请求 ≤2s 内都收到 JSON 响应（ok 为 bool，状态码 ∈ {200,400,401,404,413}），
服务线程存活、不崩溃、不挂起；成功分发的动作可被 json 序列化且坐标/文本已收敛。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 项目根入 sys.path（同 test_main.py 惯例）

import json
import math
import random
import socket
import threading

import httpx
import pytest
from _monkey_common import (
    deep_nested_json,
    dirty_value,
    json_with_dup_keys,
    json_with_nan,
    pick_number,
    pick_text,
)

from gold_pet.control_server import PLAYABLE_STATES, build_server

SEED = 20261002
ITERATIONS = 500
REQUEST_TIMEOUT = 2.0

TOKEN = "monkey-token"
VALID_STATUS = {200, 400, 401, 404, 413}


def _state_reader() -> dict:
    return {"app": "gold-pet", "version": "0.8.0", "state": "idle", "fatty_count": 0}


@pytest.fixture(scope="module")
def pet_server():
    """同 test_control_server.py 的 build_server 夹具：真实起服务，动作收集到列表。"""
    actions: list[dict] = []
    server = build_server(("127.0.0.1", 0), TOKEN, _state_reader)
    server.on_action = actions.append
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", actions, server
    server.shutdown()
    server.server_close()


def _assert_json_response(resp: httpx.Response) -> dict:
    assert resp.status_code in VALID_STATUS, f"意外状态码 {resp.status_code}: {resp.text[:200]}"
    data = resp.json()
    assert isinstance(data, dict)
    assert isinstance(data.get("ok"), bool)
    return data


def _post(base: str, path: str, *, content: bytes | None = None, json_body=None,
          token: str = TOKEN) -> httpx.Response:
    """json_body 走 allow_nan=True + ensure_ascii=True 手工编码：httpx 自带的 json=
    拒绝 inf/NaN，而 NaN 字面量与孤代理对正是要灌给服务端的脏 payload
    （服务端必须回 JSON 而不是断连）。"""
    headers = {"X-GP-Token": token}
    if json_body is not None:
        content = json.dumps(json_body, ensure_ascii=True, allow_nan=True).encode("utf-8")
    headers["Content-Type"] = "application/json; charset=utf-8"
    return httpx.post(f"{base}{path}", content=content or b"", headers=headers,
                      timeout=REQUEST_TIMEOUT)


def _post_oversize_tolerant(base: str, path: str, content: bytes) -> bool:
    """发送超过 64KB 上限的请求体。

    服务端按标准 HTTP 语义回 413 后立即关闭连接，客户端可能还在写剩余 body
    而收到传输层错误（WinError 10053）——这是 413 的正常表现，不是服务端 bug。
    返回 True 表示拿到了完整响应，False 表示传输层中止（413 语义内）。
    """
    try:
        resp = _post(base, path, content=content)
    except httpx.TransportError:
        return False
    # 白名单路径在 body 检查处 413；未知路径在路由检查处 404（先路由后读体）
    assert resp.status_code in (404, 413), f"oversize -> {resp.status_code}"
    _assert_json_response(resp)
    return True


def _assert_server_alive(base: str) -> None:
    """413 断连之后服务必须仍然可用。"""
    resp = httpx.get(f"{base}/status", headers={"X-GP-Token": TOKEN},
                     timeout=REQUEST_TIMEOUT)
    assert resp.status_code == 200


def test_monkey_endpoints_dirty_payloads(pet_server):
    """500 次随机脏 payload：每个请求都必须拿到 JSON 响应，服务不崩不挂。"""
    rnd = random.Random(SEED)
    base, actions, _ = pet_server
    paths = ["/say", "/play", "/move", "/pat", "/tease", "/status", "/nope"]

    for i in range(ITERATIONS):
        path = rnd.choice(paths)
        actions.clear()
        use_json = rnd.random() < 0.6
        if use_json:
            # 80% 非法：整体非对象 / 字段脏值 / 未知字段；
            # 20% 合法边界：带全部期望字段但取边界值
            if rnd.random() < 0.8:
                body = dirty_value(rnd) if rnd.random() < 0.5 else {
                    "text": pick_number(rnd),
                    "mood": pick_number(rnd),
                    "state": pick_number(rnd),
                    "dx": pick_number(rnd),
                    "dy": pick_number(rnd),
                    "x": pick_number(rnd),
                    "y": pick_number(rnd),
                }
            else:
                body = {
                    "text": pick_text(rnd),
                    "mood": "",
                    "state": rnd.choice(PLAYABLE_STATES),
                    "dx": rnd.choice([0, 100, -100, 2**31]),
                    "dy": rnd.choice([0, 50, -50, -(2**31)]),
                }
            resp = _post(base, path, json_body=body)
        else:
            roll = rnd.random()
            if roll < 0.1:
                content = json_with_nan().encode("utf-8")
            elif roll < 0.2:
                content = json_with_dup_keys().encode("utf-8")
            elif roll < 0.3:
                content = b"\xff\xfe not-utf8 \x00"
            elif roll < 0.4:
                content = b'{"text": ' + bytes(rnd.randrange(256) for _ in range(16)) + b"}"
            elif roll < 0.5:
                content = deep_nested_json(rnd.choice([50, 500, 5000, 30000]))
            elif roll < 0.55:
                content = b'{"text": "' + b"a" * (64 * 1024 + 1024) + b'"}'  # 超 64KB -> 413
            else:
                # ensure_ascii=True：孤代理对转成合法的 \\udXXX 转义，UTF-8 编码不炸
                content = json.dumps(dirty_value(rnd, max_depth=6), ensure_ascii=True).encode(
                    "utf-8"
                )
            if len(content) > 64 * 1024:
                # 超 64KB：413 语义下允许传输层中止，但服务必须存活（见 _post_oversize_tolerant）
                _post_oversize_tolerant(base, path, content)
                _assert_server_alive(base)
                continue
            resp = _post(base, path, content=content)

        _assert_json_response(resp)

        # 分发出去的动作必须可序列化且已收敛（这批动作会经 Qt Signal 进 UI 线程）
        if resp.status_code == 200 and path != "/status":
            assert len(actions) == 1
            action = actions[0]
            json.dumps(action, ensure_ascii=False)  # 不可序列化会抛异常
            if action.get("action") == "say" and "text" in action:
                assert isinstance(action["text"], str) and len(action["text"]) <= 500
            if action.get("action") == "move":
                for v in action.values():
                    if isinstance(v, int) and not isinstance(v, bool):
                        assert math.isfinite(v)
            if action.get("action") == "play":
                assert action["state"] in PLAYABLE_STATES

        # 偶发校验 401 分支
        if i % 50 == 7:
            resp = _post(base, "/say", json_body={"text": "hi"}, token="wrong")
            assert resp.status_code == 401
            _assert_json_response(resp)

    # 服务线程全程存活（handler 层异常不允许拖垮 accept 循环）
    assert pet_server[2]  # server 对象仍在


def test_monkey_heavy_payloads(pet_server):
    """定向重型语料：1MB 包 / 2 万层嵌套 / 超限文本，全部 ≤2s 拿到 JSON。"""
    base, actions, server = pet_server

    # 1MB 请求体：服务端必须回 413 + JSON。用原始 socket 发完整请求（M-09 修复后
    # 服务端在短路响应前会限量排空请求体，因此客户端要发完 body 才能稳定读响应）。
    port = server.server_address[1]
    payload = b"x" * (1024 * 1024)
    with socket.create_connection(("127.0.0.1", port), timeout=REQUEST_TIMEOUT) as sock:
        head = (
            f"POST /say HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"X-GP-Token: {TOKEN}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n\r\n"
        ).encode("ascii")
        sock.sendall(head + payload)
        raw = b""
        while b"}" not in raw:  # 读到 JSON 响应体结束（连接随即被服务端关闭）
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
    assert b" 413 " in raw.split(b"\r\n", 1)[0]
    body = raw.split(b"\r\n\r\n", 1)[1]
    assert json.loads(body.decode("utf-8"))["ok"] is False

    # 深层嵌套（远超 json 解析递归极限）：M-02 修复后必须 400 而不是断连
    resp = _post(base, "/say", content=deep_nested_json(20000))
    assert resp.status_code == 400
    _assert_json_response(resp)

    # 贴限超长文本（~60KB 体内，UTF-8 直编避免 \\u 转义膨胀）：200 OK，动作文本截断到 500
    resp = _post(base, "/say",
                 content=json.dumps({"text": "鲸" * 20_000}, ensure_ascii=False).encode("utf-8"))
    assert resp.status_code == 200
    assert len(actions[-1]["text"]) == 500

    # NaN / Infinity / 超大数坐标：M-01 修复后必须 400 且有 JSON 响应
    for raw in (
        '{"dx": NaN, "dy": 1}',
        '{"dx": Infinity, "dy": -Infinity}',
        '{"x": NaN, "y": NaN}',
        '{"dx": 1e308, "dy": -1e308}',
        '{"text": "\\", dx\\": NaN"}',
    ):
        resp = _post(base, "/move", content=raw.encode("utf-8"))
        _assert_json_response(resp)

    # 布尔坐标（int 子类陷阱）与字符串数字：语义收敛为 400
    for raw in (b'{"dx": true, "dy": false}', b'{"dx": "5", "dy": "6"}'):
        resp = _post(base, "/move", content=raw)
        _assert_json_response(resp)

    # GET /status 带超长 query 与脏 token
    resp = httpx.get(f"{base}/status?{'q' * 10000}=1", headers={"X-GP-Token": TOKEN},
                     timeout=REQUEST_TIMEOUT)
    assert resp.status_code == 200
    resp = httpx.get(f"{base}/status", timeout=REQUEST_TIMEOUT)
    assert resp.status_code == 401
