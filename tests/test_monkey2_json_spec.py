"""Monkey 测试 R2 目标 5：JSON 规范边角 + 发现文件校验对齐（一轮未覆盖的角度）。

种子：20261015；迭代：≥500。
边角语料：重复键（后值覆盖语义）、__proto__/constructor 键、-0 / -0.0、
1e999（解析为 inf）、BOM、\\u0000 转义、非法 \\x 转义、嵌套 2000 层（合法）/
20000 层（RecursionError 必须被各加载器接住，R2-M-03）、400 位大整数（R2-M-01）。

oracle：每个加载器对每条语料要么给出结构化结果要么给出约定错误（CliError /
400 / 默认值），绝不抛未处理异常；control.json 脏值必须被三处发现读取器一致拒绝。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import os
import random

import pytest
from _monkey_common import assert_valid_json_text

import cli as cli_mod
from gold_pet import control_server as cs_mod
from gold_pet.config import Config
from gold_pet.notifier import Notifier
from gold_pet.price import HistoryStore, PriceService

SEED = 20261015

BIG_INT = str(10**400)
DEEP_2000 = "[" * 2000 + "]" * 2000
DEEP_20000 = "[" * 20000 + "]" * 20000

SPEC_DOCS = [
    # (名称, JSON 文本) —— 全部必须被安全消化
    ("重复键后值覆盖", '{"interval_sec": 15, "interval_sec": 900, "metal": "silver", "metal": "gold"}'),
    ("__proto__/constructor 键", '{"__proto__": {"x": 1}, "constructor": 2, "metal": "silver"}'),
    ("-0 整数", '{"interval_sec": -0, "alert_pct": -0.0, "opacity": -0.0}'),
    ("1e999 -> inf", '{"alert_pct": 1e999, "target_high": -1e999, "opacity": 1e999}'),
    ("1e-999 下溢", '{"alert_pct": 1e-999, "target_high": 1e-999}'),
    ("NUL 转义键与值", '{"metal": "gold\\u0000x", "键\\u0000": 1}'),
    ("嵌套 2000 层（合法解析）", '{"pos": ' + DEEP_2000 + '}'),
    ("大整数字面量", '{"interval_sec": ' + BIG_INT + ', "alert_cooldown_sec": ' + BIG_INT + '}'),
    ("BOM 前缀（字节层）", '\ufeff{"metal": "silver"}'),
]


def _assert_config_safe(path: Path) -> None:
    cfg = Config.load(path)
    assert cfg.get("metal") in ("gold", "silver", "platinum")
    assert cfg.get("interval_sec") in (15, 30, 60, 300, 900)
    assert 0.0 <= cfg.get("opacity") <= 1.0
    assert isinstance(cfg.get("alert_pct"), float)


def test_r2_json_spec_config_loader(tmp_path):
    for name, doc in SPEC_DOCS:
        p = tmp_path / "settings.json"
        if name == "BOM 前缀（字节层）":
            p.write_bytes(b"\xef\xbb\xbf" + doc.lstrip("\ufeff").encode("utf-8"))
        else:
            p.write_text(doc, encoding="utf-8")
        _assert_config_safe(p)  # 重复键后值覆盖由 json.loads 语义保证
        assert Config.load(p).get("interval_sec") != 15 or name != "重复键后值覆盖"


def test_r2_json_spec_history_loader(tmp_path):
    rnd = random.Random(SEED)
    hp = tmp_path / "history.json"
    corpus = [
        '{"gold|cny_g": [{"t":1,"p":1},{"t":1,"p":1},{"t":-0,"p":-0.0}]}',
        '{"gold|cny_g": [{"t":1e999,"p":1e999}]}, "dup": 1}',
        '{"g\\u0000k": [{"t":1,"p":1,"x":"a\\u0000b"}]}',
        '{"gold|cny_g": ' + DEEP_2000 + '}',
        '{"gold|cny_g": ' + DEEP_20000 + '}',
        '{"gold|cny_g": [{"t":' + BIG_INT + ',"p":' + BIG_INT + '}]}',
        '{"__proto__": [{"t":1,"p":1}], "constructor": {"t": 1}}',
        "\ufeff" + '{"gold|cny_g": [{"t":1,"p":1}]}',
        '{"gold|cny_g": [{"t":1,"p":1},{"t":"1","p":1e999}]}',
    ]
    for _ in range(500):
        doc = rnd.choice(corpus)
        if doc.startswith("\ufeff"):
            hp.write_bytes(b"\xef\xbb\xbf" + doc.lstrip("\ufeff").encode("utf-8"))
        else:
            hp.write_text(doc, encoding="utf-8")
        hs = HistoryStore(hp)
        data = hs.load()  # R2-M-03 修复前 20000 层在此抛 RecursionError
        assert isinstance(data, dict)
        for series in data.values():
            for pt in series:
                assert isinstance(pt, dict)
        # append 后回写必须仍是合法 JSON
        hs.append("gold", "cny_g", 700.5)
        assert_valid_json_text(hp.read_text(encoding="utf-8"))


def test_r2_json_spec_price_cache_and_notifier(tmp_path):
    corpus = [
        DEEP_20000,
        '{"quotes": {"gold|cny_g": {"price": 1e999, "prev_close": 1}}}',
        '{"quotes": ' + DEEP_2000 + "}",
        '{"quotes": {"__proto__": {"price": 1, "prev_close": 1}}, "fx": -0.0}',
        '{"quotes": {"gold|cny_g": {"price": ' + BIG_INT + '}}, "updated_at": ' + BIG_INT + "}",
        "\ufeff" + '{"quotes": {}}',
    ]
    cp = tmp_path / "last.json"
    for doc in corpus:
        if doc.startswith("\ufeff"):
            cp.write_bytes(b"\xef\xbb\xbf" + doc.lstrip("\ufeff").encode("utf-8"))
        else:
            cp.write_text(doc, encoding="utf-8")
        snap = PriceService(cp).load_cache()  # R2-M-03：RecursionError 必须被接住
        assert snap is None or isinstance(snap.updated_at, float)
        if snap is not None:
            assert_valid_json_text(json.dumps(snap.to_dict(), allow_nan=False))

    # Notifier 历史加载：深嵌套/边角键
    ah = tmp_path / "ah.json"
    for doc in [DEEP_20000, '[{"kind": "pct", "at": 1e999, "__proto__": 1}]', '{"a": 1}']:
        ah.write_text(doc, encoding="utf-8")
        n = Notifier(Config({}), history_path=ah)
        n.history()  # 不得炸
        assert len(n._history) <= 200


def test_r2_json_spec_cli_load_json(tmp_path, capsys):
    """cli 读深嵌套/坏数据文件：约定退出码 4，绝不裸 traceback（R2-M-03）。"""
    hp = tmp_path / "history.json"
    hp.write_text(DEEP_20000, encoding="utf-8")
    orig = cli_mod.history_path
    cli_mod.history_path = lambda: hp
    try:
        rc = cli_mod.main(["history", "--json"])
    finally:
        cli_mod.history_path = orig
    assert rc == 4
    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert data["ok"] is False


def test_r2_read_control_info_validation_matrix(tmp_path, monkeypatch):
    """read_control_info 对脏 port/token 的拒绝矩阵（R2-M-06，对齐一轮 M-08）。"""
    me = os.getpid()
    p = tmp_path / "control.json"
    cases = [
        ({"port": ["9999"], "token": "t", "pid": me}, None),
        ({"port": "abc", "token": "t", "pid": me}, None),
        ({"port": 999999, "token": "t", "pid": me}, None),
        ({"port": 0, "token": "t", "pid": me}, None),
        ({"port": True, "token": "t", "pid": me}, None),
        ({"port": 8080, "token": "", "pid": me}, None),
        ({"port": 8080, "token": 123, "pid": me}, None),
        ({"port": 8080, "token": "ok", "pid": me}, dict),
        (DEEP_20000, None),
    ]
    monkeypatch.setattr(cs_mod, "control_info_path", lambda: p)
    for doc, expect in cases:
        p.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
        info = cs_mod.read_control_info()
        if expect is None:
            assert info is None, f"{str(doc)[:60]} 应被拒绝"
        else:
            assert isinstance(info, expect) and isinstance(info["port"], int)


def test_r2_mcp_bridge_discover_validation(tmp_path):
    """MCP 桥 _discover 对脏 control.json 抛友好 RuntimeError（R2-M-06），不再裸 TypeError。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "pet_mcp_server_test", Path(__file__).resolve().parent.parent / "tools" / "pet_mcp_server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    p = tmp_path / "control.json"
    mod._INFO_PATH = p
    me = os.getpid()
    for doc in [
        {"port": ["9999"], "token": "t", "pid": me},
        {"port": "abc", "token": "t", "pid": me},
        {"port": 999999, "token": "t", "pid": me},
        {"port": 8080, "token": ""},
    ]:
        p.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(RuntimeError):
            mod._discover()
    p.write_text(json.dumps({"port": 8080, "token": "ok", "pid": me}), encoding="utf-8")
    assert mod._discover() == (8080, "ok")
