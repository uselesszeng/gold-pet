"""Monkey 测试 R3 目标 6：Mimosa 扫描语料定向复核（8 条 finding 当作待验证假设）。

种子：20261026。
Mimosa 扫描（project-19ddff75e31ce2260e9b86ea，scan-2026-10-04T07-17-16.204Z，
runStatus=inconclusive，8 条 finding：2 low insecure-randomness + 1 high path-traversal
advisory + 5 high SSRF）不直接采信：每条写定向测试实测「能否复现」。能复现才立 bug，
不能复现则在报告记录「未复现」及原因。

假设 → 测试映射：
  H1  gold_pet/ui.py:258  random.choice(FATTY_LINES)   不安全随机数
  H2  gold_pet/ui.py:768  random.choice(LINES)         不安全随机数
  H3  tools/import_dsh_assets.py:103 frames_of         路径穿越入口
  H4  cli.py:157          urlopen(req)                 SSRF（动态 URL）
  H5  gold_pet/price.py:254 urlopen(req)               SSRF（动态 URL）
  H6  tools/cnb_release_upload.py:62 urlopen           SSRF
  H7  tools/cnb_release_upload.py:86 urlopen           SSRF
  H8  tools/pet_mcp_server.py:82 httpx.request         SSRF

oracle：结论必须由可重复的运行时断言支撑；URL 类假设检查 scheme/host/path 是否
被固定白名单锚定；随机数类假设检查用途是否属于安全上下文（token/密钥/权限）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import cli as cli_mod
from gold_pet import price as price_mod
from gold_pet import ui as ui_mod

SEED = 20261026
ITERATIONS = 200
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIMOSA_DIR = Path(
    r"C:\Users\tj-435\.mimosa\security-scans\project-19ddff75e31ce2260e9b86ea"
    r"\scan-2026-10-04T07-17-16.204Z-95cc34778526"
)


class _FakeResp:
    def __init__(self, body: bytes = b"{}", status: int = 200):
        self._body = body
        self.status = status
        self.status_code = status

    def read(self) -> bytes:
        return self._body

    def json(self):
        return json.loads(self._body)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _load_tool(name: str):
    path = PROJECT_ROOT / "tools" / name
    spec = importlib.util.spec_from_file_location(f"r3_{Path(name).stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ H1/H2 随机数
def test_r3_mimosa_h1_fatty_lines_random_not_security():
    """H1 复核：ui.py:258 的 random.choice 只用于台词挑选，非安全上下文 → 未复现。"""
    rnd = random.Random(SEED)
    pool = [line for lines in ui_mod.FATTY_LINES.values() for line in lines]
    for _ in range(ITERATIONS * 2):
        count = rnd.choice([0, 1, 2, 3, 4, 5, 6, 50, 10**9, -7])
        line = ui_mod.pick_fatty_line(count)
        assert line in pool, "台词选择不得产出池外内容"

    # 安全 token 走 secrets 而非 random（控制服务源码为证）
    import inspect

    from gold_pet import control_server as cs

    src = inspect.getsource(cs.ControlServer)
    assert "secrets.token_urlsafe" in src
    assert "random." not in src


def test_r3_mimosa_h2_speak_random_not_security(tmp_path):
    """H2 复核：ui.py:768 的 random.choice 只用于台词挑选；非法 mood 回落 idle → 未复现。"""
    import html

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from gold_pet.config import Config
    from gold_pet.price import HistoryStore, PriceService
    from gold_pet.ui import LINES, PetWindow

    QApplication.instance() or QApplication([])
    cfg = Config()
    cfg.set("control_enabled", False)
    w = PetWindow(cfg, PriceService(tmp_path / "l.json"), HistoryStore(tmp_path / "h.json"),
                  auto_refresh=False, notifier_history_path=tmp_path / "a.json")
    try:
        rnd = random.Random(SEED + 1)
        moods = list(LINES) + ["nope", "", "NaN", None]
        for _ in range(ITERATIONS):
            mood = rnd.choice(moods)
            w.speak(mood)
            text = w.bubble.text()
            expected_pool = LINES.get(mood, LINES["idle"])
            assert any(html.escape(line) in text for line in expected_pool)
        # 池外内容不可能出现在台词里（随机选择只消费内置池）
        assert all(isinstance(k, str) and v for k, v in LINES.items())
    finally:
        w.deleteLater()


# ------------------------------------------------------------------ H3 路径穿越
def test_r3_mimosa_h3_frames_of_no_privilege_escape(tmp_path, monkeypatch):
    """H3 复核：frames_of 的入参是调用者自己的目录，输出路径固定 → 未复现（无越权写）。

    验证方式：用真实 GIF 跑一遍工具 main()，所有产出必须落在被改写后的
    PET_DIR/ICON 路径（模拟的工程根）之内；带 `..` 的 src 参数只影响读取来源，
    不影响写入位置。真实工程 assets 目录必须零改动。
    """
    Image = pytest.importorskip("PIL.Image")
    mod = _load_tool("import_dsh_assets.py")

    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    for gif_name in set(mod.STATE_MAP.values()):
        frames = [Image.new("RGBA", (10, 12), (255, 0, 0, 255)),
                  Image.new("RGBA", (10, 12), (0, 255, 0, 255))]
        frames[0].save(src / f"{gif_name}.gif", save_all=True,
                       append_images=frames[1:], duration=100, loop=0)

    fake_root = tmp_path / "fake_root"
    pet_dir = fake_root / "assets" / "pet"
    pet_dir.mkdir(parents=True)
    (pet_dir / "old_0.png").write_bytes(b"old")
    fake_root.joinpath("assets", "icon.png").write_bytes(b"old-icon")
    fake_root.joinpath("assets", "icon.ico").write_bytes(b"old-icon")

    monkeypatch.setattr(mod, "ROOT", fake_root)
    monkeypatch.setattr(mod, "PET_DIR", pet_dir)
    monkeypatch.setattr(mod, "BACKUP_DIR", fake_root / "assets" / "_backup_v055")
    real_assets = PROJECT_ROOT / "assets"
    before = {p.relative_to(real_assets) for p in real_assets.rglob("*")}

    src_with_dots = str(src / "sub" / "..")  # 含 .. 的 CLI 参数，解析后仍是 src
    monkeypatch.setattr(sys, "argv", ["import_dsh_assets.py", src_with_dots])
    assert mod.main() == 0

    after = {p.relative_to(real_assets) for p in real_assets.rglob("*")}
    assert before == after, "工具写到了真实工程 assets 之外的位置"
    produced = [p for p in fake_root.rglob("*") if p.is_file()]
    assert produced, "应产出序列帧"
    assert all(fake_root in p.parents for p in produced), "产出逃出固定 PET_DIR"
    # 旧素材被替换在新的 PET_DIR 内，而不是工程目录
    assert not (pet_dir / "old_0.png").exists()


# ------------------------------------------------------------------ H4/H5 主程序 SSRF
def test_r3_mimosa_h4_cli_url_loopback_and_whitelist(tmp_path, monkeypatch, capsys):
    """H4 复核：cli.py 的动态 URL 只可能落在 127.0.0.1 + 固定白名单路径 → 未复现。"""
    info = tmp_path / "control.json"
    info.write_text(json.dumps({"port": 45999, "token": "tok", "pid": os.getpid()}),
                    encoding="utf-8")
    monkeypatch.setattr(cli_mod, "control_info_path", lambda: info)
    # 本地数据文件也隔离到 tmp，避免读到真实 %APPDATA%
    monkeypatch.setattr(cli_mod, "cache_path", lambda: tmp_path / "last_price.json")
    monkeypatch.setattr(cli_mod, "history_path", lambda: tmp_path / "history.json")
    monkeypatch.setattr(cli_mod, "alert_history_path", lambda: tmp_path / "alert_history.json")
    from gold_pet import config as config_mod

    monkeypatch.setattr(config_mod, "settings_path", lambda: tmp_path / "settings.json")

    captured: list[str] = []

    def fake_urlopen(req, timeout=None):
        captured.append(req.full_url)
        return _FakeResp(b'{"ok": true, "mode": "text"}')

    monkeypatch.setattr(cli_mod.urllib.request, "urlopen", fake_urlopen)
    commands = [
        (["status", "--json"], "/status"),
        (["say", "hi", "--json"], "/say"),
        (["play", "idle", "--json"], "/play"),
        (["pat", "--json"], "/pat"),
        (["tease", "--json"], "/tease"),
        (["move", "--dx", "1", "--dy", "1", "--json"], "/move"),
    ]
    for argv, expect_path in commands:
        code = cli_mod.main(argv)
        capsys.readouterr()
        assert code == 0
        url = urlsplit(captured[-1])
        assert url.scheme == "http" and url.hostname == "127.0.0.1"
        assert url.port == 45999 and url.path == expect_path

    # parser 面没有 --host/--url 之类改目标主机的入口
    parser = cli_mod.build_parser()
    def walk(p):
        for a in p._actions:
            yield a
            if hasattr(a, "choices") and a.choices:
                for c in a.choices.values() if isinstance(a.choices, dict) else a.choices:
                    if hasattr(c, "_actions"):
                        yield from walk(c)
    dests = {a.dest for a in walk(parser)}
    assert not {d for d in dests if "host" in d or "url" in d or "endpoint" in d}


def test_r3_mimosa_h4_cli_rejects_dirty_ports(capsys, tmp_path, monkeypatch):
    """H4 补充：port 非 int/越界/str/bool → 结构化 exit 4，不进任何请求。"""
    info = tmp_path / "control.json"
    monkeypatch.setattr(cli_mod, "control_info_path", lambda: info)
    called: list[str] = []
    monkeypatch.setattr(cli_mod.urllib.request, "urlopen",
                        lambda req, timeout=None: called.append(req.full_url))
    for bad_port in [0, -1, 65536, 99999, True, "80", 3.14, None, [80], {"p": 80}]:
        info.write_text(json.dumps({"port": bad_port, "token": "t", "pid": os.getpid()}),
                        encoding="utf-8")
        code = cli_mod.main(["status", "--json"])
        out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert code == 4 and out["ok"] is False
    assert called == [], "脏 port 不得发起任何请求"


def test_r3_mimosa_h5_price_fetch_url_is_constant(monkeypatch):
    """H5 复核：price.fetch_raw 的目标 URL 是模块常量，唯一入参是 timeout → 未复现。"""
    import inspect

    params = list(inspect.signature(price_mod.fetch_raw).parameters)
    assert params == ["timeout"]

    captured: list[str] = []

    def fake_urlopen(req, timeout=None):
        captured.append(req.full_url)
        return _FakeResp(b'var hq_str_gds_AUTD="900,0,0,0,0,0,0,0,0,0,0,0,0,x";')

    monkeypatch.setattr(price_mod.urllib.request, "urlopen", fake_urlopen)
    price_mod.fetch_raw(timeout=0.5)
    url = urlsplit(captured[-1])
    assert captured[-1] == price_mod.SINA_URL
    assert url.scheme == "https" and url.hostname == "hq.sinajs.cn"
    # host 不可由参数/环境改写：SINA_URL 是字面量常量
    assert price_mod.SINA_URL.startswith("https://hq.sinajs.cn/")


# ------------------------------------------------------------------ H6/H7 CNB 工具
def test_r3_mimosa_h6_cnb_api_host_pinned(monkeypatch):
    """H6 复核：slug/release_id 注入不改变 host（固定 https://api.cnb.cool 前缀）→ 未复现。"""
    mod = _load_tool("cnb_release_upload.py")
    captured: list[str] = []

    def fake_urlopen(req, timeout=None):
        captured.append(req.full_url)
        return _FakeResp(json.dumps({"upload_url": "u", "verify_url": "v"}).encode())

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    adversarial = [
        "evil.com@attacker.test", "//evil.com", "../../evil", "https://evil.com",
        "x#frag", "x?q=1", "a b", "..%2f..%2f", "\nHost: evil.test",
        "api.cnb.cool@evil.test", "\\\\evil.test", "例子.中国",
    ]
    rnd = random.Random(SEED)
    for slug in adversarial + [f"repo-{rnd.randint(0, 10**6)}" for _ in range(ITERATIONS)]:
        captured.clear()
        try:
            mod.get_upload_url(slug, "rel-1", "a.bin", 10, "tok")
        except ValueError:
            # 控制字符等让 urllib 在发请求前就拒绝（比放行更安全）：无请求即无泄漏
            assert captured == []
            continue
        url = urlsplit(captured[-1])
        assert url.scheme == "https" and url.hostname == "api.cnb.cool", (slug, captured[-1])


def test_r3_mimosa_h7_cnb_put_file_follows_api_response(monkeypatch, tmp_path):
    """H7 复核：put_file 的 URL 来自 CNB API 响应（设计内信任边界）→ 未复现，
    但记录「响应被控时文件会被 PUT 到任意外部地址」的潜在条件（本次无证据）。"""
    mod = _load_tool("cnb_release_upload.py")
    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * 32)
    captured: list[str] = []

    def fake_urlopen(req, timeout=None):
        captured.append(req.full_url)
        return _FakeResp(b"", 200)

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    assert mod.put_file("https://oss.example.test/presigned?k=1", f) == 200
    assert captured[-1] == "https://oss.example.test/presigned?k=1"  # 原样跟随（预签名流程要求）
    # 函数签名无 host 参数；URL 唯一来源是 API 响应字段
    import inspect

    assert list(inspect.signature(mod.put_file).parameters) == ["upload_url", "path"]


# ------------------------------------------------------------------ H8 MCP 桥
def test_r3_mimosa_h8_mcp_bridge_loopback(tmp_path, monkeypatch):
    """H8 复核：MCP 桥的 httpx.request 目标恒为 http://127.0.0.1:{port}{白名单path} → 未复现。"""
    pytest.importorskip("mcp")
    mod = _load_tool("pet_mcp_server.py")
    info = tmp_path / "control.json"
    info.write_text(json.dumps({"port": 45999, "token": "tok", "pid": os.getpid()}),
                    encoding="utf-8")
    monkeypatch.setattr(mod, "_INFO_PATH", info)

    captured: list[str] = []

    def fake_request(method, url, **kw):
        captured.append(url)
        return _FakeResp(json.dumps({"ok": True, "message": "done"}).encode())

    monkeypatch.setattr(mod.httpx, "request", fake_request)
    assert mod._call("POST", "/say", {"text": "hi"}) == "done"
    assert mod.pet_pat() == "done"
    assert mod.pet_move(dx=3, dy=4) == "done"
    for url in captured:
        parts = urlsplit(url)
        assert parts.scheme == "http" and parts.hostname == "127.0.0.1" and parts.port == 45999
        assert parts.path in ("/say", "/pat", "/move")


# ------------------------------------------------------------------ 扫描语料一致性
def test_r3_mimosa_scan_corpus_matches():
    """扫描语料与复测清单一致性（目录不可读则跳过并注明）。"""
    findings_path = MIMOSA_DIR / "findings.json"
    if not findings_path.is_file():
        pytest.skip("Mimosa 扫描目录不可读，跳过语料一致性检查（报告注明）")
    data = json.loads(findings_path.read_text(encoding="utf-8"))
    findings = data["findings"]
    assert len(findings) == 8
    locations = {(f["location"]["path"], f["location"]["line"]) for f in findings}
    assert locations == {
        ("gold_pet/ui.py", 258), ("gold_pet/ui.py", 768),
        ("tools/import_dsh_assets.py", 103),
        ("cli.py", 157), ("gold_pet/price.py", 254),
        ("tools/cnb_release_upload.py", 62), ("tools/cnb_release_upload.py", 86),
        ("tools/pet_mcp_server.py", 82),
    }
    # 扫描状态为 inconclusive（verdictEffect=none）：结论仅作假设输入
    assert data["verdictEffect"] == "none"
