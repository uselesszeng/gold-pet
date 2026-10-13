"""AI 控制服务测试（v0.8）：协议层 + 发现文件 + 配置开关。

协议层测试不起 QThread：直接用 build_server 构建纯 socket 层 HTTP 服务，
daemon 线程跑 serve_forever，httpx 当客户端，on_action 用列表收集动作。
"""

from __future__ import annotations

import json
import os
import threading

import httpx
import pytest

from gold_pet import control_server
from gold_pet.config import Config
from gold_pet.control_server import PLAYABLE_STATES, _pid_alive, build_server, read_control_info

TOKEN = "test-token-abc"


def _state_reader() -> dict:
    return {
        "app": "gold-pet",
        "version": "0.8.0",
        "state": "idle",
        "activity_level": "normal",
        "fatty_count": 0,
    }


@pytest.fixture()
def pet_server():
    """起一个真实 HTTP 服务（随机端口），返回 (base_url, 动作收集列表)。"""
    actions: list[dict] = []
    server = build_server(("127.0.0.1", 0), TOKEN, _state_reader)
    server.on_action = actions.append
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", actions
    server.shutdown()
    server.server_close()


# ------------------------------------------------------------------ 鉴权
def test_status_without_token_401(pet_server):
    base, _ = pet_server
    resp = httpx.get(f"{base}/status", timeout=2)
    assert resp.status_code == 401


def test_status_wrong_token_401(pet_server):
    base, _ = pet_server
    resp = httpx.get(f"{base}/status", headers={"X-GP-Token": "wrong"}, timeout=2)
    assert resp.status_code == 401


# ------------------------------------------------------------------ /status
def test_status_ok(pet_server):
    base, _ = pet_server
    resp = httpx.get(f"{base}/status", headers={"X-GP-Token": TOKEN}, timeout=2)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["app"] == "gold-pet"
    assert data["state"] == "idle"
    assert data["fatty_count"] == 0


# ------------------------------------------------------------------ /say
def test_say_text_dispatch(pet_server):
    base, actions = pet_server
    resp = httpx.post(
        f"{base}/say", json={"text": "主人好呀"}, headers={"X-GP-Token": TOKEN}, timeout=2
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert actions == [{"action": "say", "text": "主人好呀"}]


def test_say_mood_dispatch(pet_server):
    base, actions = pet_server
    resp = httpx.post(f"{base}/say", json={"mood": "up"}, headers={"X-GP-Token": TOKEN}, timeout=2)
    assert resp.status_code == 200
    assert actions == [{"action": "say", "mood": "up"}]


def test_say_missing_params_400(pet_server):
    base, actions = pet_server
    resp = httpx.post(f"{base}/say", json={}, headers={"X-GP-Token": TOKEN}, timeout=2)
    assert resp.status_code == 400
    assert actions == []


def test_say_blank_text_400(pet_server):
    base, _ = pet_server
    resp = httpx.post(f"{base}/say", json={"text": "   "}, headers={"X-GP-Token": TOKEN}, timeout=2)
    assert resp.status_code == 400


def test_say_text_length_capped(pet_server):
    """P2-01：自由发言限长（500 字符），超长文本截断，不撑爆气泡。"""
    base, actions = pet_server
    resp = httpx.post(
        f"{base}/say", json={"text": "啊" * 1000}, headers={"X-GP-Token": TOKEN}, timeout=2
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert len(actions) == 1
    assert len(actions[0]["text"]) == 500


# ------------------------------------------------------------------ /play
def test_play_dispatch(pet_server):
    base, actions = pet_server
    resp = httpx.post(
        f"{base}/play", json={"state": "shy"}, headers={"X-GP-Token": TOKEN}, timeout=2
    )
    assert resp.status_code == 200
    assert actions == [{"action": "play", "state": "shy"}]


def test_play_rejects_non_performance_state(pet_server):
    """loading/offline 是行情驱动状态，不允许外部乱设。"""
    base, actions = pet_server
    assert "loading" not in PLAYABLE_STATES
    resp = httpx.post(
        f"{base}/play", json={"state": "loading"}, headers={"X-GP-Token": TOKEN}, timeout=2
    )
    assert resp.status_code == 400
    assert actions == []


# ------------------------------------------------------------------ /pat /tease
def test_pat_and_tease_dispatch(pet_server):
    base, actions = pet_server
    for path, action in (("/pat", "pat"), ("/tease", "tease")):
        resp = httpx.post(f"{base}{path}", json={}, headers={"X-GP-Token": TOKEN}, timeout=2)
        assert resp.status_code == 200
        assert actions[-1] == {"action": action}


# ------------------------------------------------------------------ /move
def test_move_relative_dispatch(pet_server):
    base, actions = pet_server
    resp = httpx.post(
        f"{base}/move", json={"dx": 100, "dy": -40}, headers={"X-GP-Token": TOKEN}, timeout=2
    )
    assert resp.status_code == 200
    assert actions == [{"action": "move", "dx": 100, "dy": -40}]


def test_move_absolute_dispatch(pet_server):
    base, actions = pet_server
    resp = httpx.post(
        f"{base}/move", json={"x": 10, "y": 20}, headers={"X-GP-Token": TOKEN}, timeout=2
    )
    assert resp.status_code == 200
    assert actions == [{"action": "move", "x": 10, "y": 20}]


def test_move_missing_params_400(pet_server):
    base, actions = pet_server
    resp = httpx.post(f"{base}/move", json={}, headers={"X-GP-Token": TOKEN}, timeout=2)
    assert resp.status_code == 400
    assert actions == []


# ------------------------------------------------------------------ 其他路由
def test_unknown_path_404(pet_server):
    base, _ = pet_server
    resp = httpx.post(f"{base}/exec", json={}, headers={"X-GP-Token": TOKEN}, timeout=2)
    assert resp.status_code == 404
    resp = httpx.get(f"{base}/exec", headers={"X-GP-Token": TOKEN}, timeout=2)
    assert resp.status_code == 404


def test_malformed_json_400(pet_server):
    base, _ = pet_server
    resp = httpx.post(
        f"{base}/say",
        content=b"not-json",
        headers={"X-GP-Token": TOKEN, "Content-Type": "application/json"},
        timeout=2,
    )
    assert resp.status_code == 400


# ------------------------------------------------------------------ 发现文件
def test_read_control_info_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(control_server, "control_info_path", lambda: tmp_path / "control.json")
    assert read_control_info() is None


def test_read_control_info_stale_pid(tmp_path, monkeypatch):
    """pid 已死的残留发现文件必须视为不可用（不能拿去连）。"""
    info_path = tmp_path / "control.json"
    info_path.write_text(
        json.dumps({"port": 1, "token": "t", "pid": 999999999, "version": "0.8.0"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(control_server, "control_info_path", lambda: info_path)
    assert read_control_info() is None


def test_pid_alive_current_process():
    assert _pid_alive(os.getpid()) is True


# ------------------------------------------------------------------ 配置开关
def test_control_enabled_default_on():
    cfg = Config({})
    assert cfg.get("control_enabled") is True


def test_control_enabled_sanitize_bool():
    cfg = Config({"control_enabled": "yes"})
    cfg.sanitize()
    assert cfg.get("control_enabled") is True
    cfg2 = Config({"control_enabled": 0})
    cfg2.sanitize()
    assert cfg2.get("control_enabled") is False
