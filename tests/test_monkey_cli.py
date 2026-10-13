"""Monkey 测试目标 6：cli.py 参数解析与脏数据文件轰炸。

种子：20261006；迭代：argv 组合 500 次 + 脏数据文件定向 120 次。
语料：未知 flag/重复参数/--json 混乱顺序/超长参数/unicode+控制字符参数，
配脏 history.json / alert_history.json / last_price.json / control.json。
80% 非法 / 20% 合法边界；所有路径文件均 monkeypatch 到 tmp，不碰真实 %APPDATA%。

oracle：不崩（除 argparse 用法错误按 SystemExit(2) 退出外不得有异常逃逸），
退出码 ∈ {0, 2, 3, 4}；--json 模式 stdout 末行必须是合法 JSON。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)  # 项目根入 sys.path（同 test_main.py 惯例）

import json
import random

import pytest

import cli as cli_mod
from gold_pet import config as config_mod

SEED = 20261006
ITERATIONS = 500

COMMANDS = [
    "status",
    "say",
    "play",
    "pat",
    "tease",
    "move",
    "price",
    "history",
    "alerts",
    "bogus",
    "",
]
STATES = ["idle", "up", "down", "pat", "shy", "loading", "NaN", "", "🐳", "--json"]
KINDS = ["pct", "target", "extreme", "bogus", "", "NaN"]


@pytest.fixture()
def cli_paths(tmp_path, monkeypatch):
    """把 CLI 依赖的全部落盘路径指到 tmp 目录。"""
    mapping = {
        "control_info_path": tmp_path / "control.json",
        "cache_path": tmp_path / "last_price.json",
        "history_path": tmp_path / "history.json",
        "alert_history_path": tmp_path / "alert_history.json",
    }
    for name, path in mapping.items():
        monkeypatch.setattr(cli_mod, name, lambda p=path: p)
    monkeypatch.setattr(config_mod, "settings_path", lambda: tmp_path / "settings.json")
    return mapping


def _run(argv: list[str], capsys):
    """运行 main；返回 (exit_code 或 None, 退出方式)。argparse 错误 -> (2, "SystemExit")。"""
    try:
        code = cli_mod.main(argv)
        return code, "return"
    except SystemExit as exc:
        assert exc.code == 2, f"argparse 之外的 SystemExit: {exc.code}"
        return 2, "SystemExit"


def _check_json_output(capsys, command: str) -> None:
    out = capsys.readouterr().out.strip()
    if not out:
        return
    last = out.splitlines()[-1]
    data = json.loads(last)  # --json 模式 stdout 末行必须可解析
    assert isinstance(data, dict) and isinstance(data.get("ok"), bool)
    assert data.get("command") == command


def test_monkey_cli_random_argv(cli_paths, capsys):
    """500 次随机 argv：退出码符合 0/2/3/4 约定，无异常逃逸。"""
    rnd = random.Random(SEED)
    junk_values = [
        "",
        " ",
        "--json",
        "-x",
        "NaN",
        "🐳",
        "\x00",
        "a" * 200,
        "-9999999999",
        "99999999999999999999",
        "--dx",
    ]

    for _ in range(ITERATIONS):
        argv: list[str] = []
        # --json 位置混乱：前置/后置/多次/不传
        json_mode = False
        if rnd.random() < 0.5:
            argv.append("--json")
            json_mode = True
        if rnd.random() < 0.3:
            argv.append("--json")
        command = rnd.choice(COMMANDS)
        if rnd.random() < 0.05:
            command = "a" * 10000  # 超长命令名
        argv.append(command)

        if command == "say":
            if rnd.random() < 0.8:
                argv.append(rnd.choice(junk_values + ["鲸" * 600]))
            if rnd.random() < 0.5:
                argv += ["--mood", rnd.choice(junk_values + ["up"])]
        elif command == "play":
            argv.append(rnd.choice(STATES))
        elif command == "move":
            for flag in ("--dx", "--dy", "--x", "--y"):
                if rnd.random() < 0.4:
                    argv += [flag, rnd.choice(junk_values + ["10", "-10", "0"])]
            if rnd.random() < 0.2:
                argv.append(rnd.choice(junk_values))  # 未知位置参数 -> exit 2
        elif command in ("price", "history"):
            if rnd.random() < 0.4:
                argv += ["--metal", rnd.choice(["gold", "silver", "platinum", "bogus", ""])]
            if rnd.random() < 0.4:
                argv += ["--unit", rnd.choice(["cny_g", "usd_oz", "bogus", ""])]
            if command == "history" and rnd.random() < 0.4:
                argv += ["--limit", rnd.choice(["0", "-5", "30", "NaN", "1" * 30])]
        elif command == "alerts":
            if rnd.random() < 0.4:
                argv += ["--limit", rnd.choice(["0", "-1", "20", "abc"])]
            if rnd.random() < 0.4:
                argv += ["--kind", rnd.choice(KINDS)]
        if rnd.random() < 0.2:
            argv.append(rnd.choice(["--unknown-flag", "-z", "--json=1"]))

        code, _how = _run(argv, capsys)
        assert code in (0, 2, 3, 4), f"argv={argv[:6]!r} 退出码 {code}"
        if json_mode:
            _check_json_output(capsys, command)
        else:
            capsys.readouterr()  # 丢弃输出，防止跨迭代串扰


def test_monkey_cli_dirty_data_files(cli_paths, capsys):
    """定向 120 次：脏数据文件 + 本地只读命令，human/json 两条输出路径都不崩。"""
    rnd = random.Random(SEED + 1)
    mapping = cli_paths
    dirty_files = [
        ("history_path", b"\xff\xfe corrupt"),
        (
            "history_path",
            b'{"gold|cny_g": [{"t": 1e30, "p": 2}, {"t": nan, "p": 3}, '
            b'"junk", null, [1], {"t": "abc", "p": "4"}, {"t": -1e30, "p": 9}]}',
        ),
        ("history_path", b'{"x": {"deep": [1, 2]}, "y": 42, "gold|cny_g": []}'),
        (
            "alert_history_path",
            b'[1, "x", null, {"at": NaN}, '
            b'{"at": 1e30, "title": "t", "body": "b"}, {"at": "123"}]',
        ),
        ("alert_history_path", b'{"not": "a list"}'),
        ("alert_history_path", b"\xef\xbb\xbf[]"),
        ("cache_path", b'{"quotes": [1, 2], "fx": "abc"}'),
        (
            "cache_path",
            b'{"quotes": {"gold|cny_g": {"price": NaN, "prev_close": NaN}}, "ok": true}',
        ),
        (
            "cache_path",
            b'{"quotes": {"gold|cny_g": {"price": 1e300, "prev_close": 0, '
            b'"high": 1, "low": 0, "name": "n", "unit": "\xe5\x85\x83"}}, "ok": true}',
        ),
        ("cache_path", b'"just a string"'),
        ("cache_path", b"\xff\xfe"),
    ]

    for _ in range(120):
        name, payload = rnd.choice(dirty_files)
        mapping[name].write_bytes(payload)

        use_json = rnd.random() < 0.5
        command = rnd.choice(["price", "history", "alerts", "status"])
        argv = (["--json"] if use_json else []) + [command]
        if rnd.random() < 0.3:
            argv += ["--limit", rnd.choice(["5", "0", "abc"])]
        code, _ = _run(argv, capsys)
        assert code in (0, 2, 3, 4), f"dirty file {name} argv={argv} -> {code}"
        if use_json:
            _check_json_output(capsys, command)
        else:
            capsys.readouterr()


def test_monkey_cli_dirty_control_info(cli_paths, capsys):
    """脏 control.json：discover 阶段必须安全失败（exit 4），绝不连出去。"""
    rnd = random.Random(SEED + 2)
    control_path = cli_paths["control_info_path"]
    payloads = [
        b"\xff\xfe",
        b"not json",
        b"[]",
        b'{"port": "abc", "token": 1}',
        b'{"port": 99999999, "token": "", "pid": -5}',
        json.dumps({"port": 1, "token": "t", "pid": 999999999}).encode(),  # pid 已死
        json.dumps({"port": 0, "token": "t"}).encode(),
    ]
    for _ in range(60):
        control_path.write_bytes(rnd.choice(payloads))
        argv = rnd.choice(
            [
                ["status"],
                ["--json", "status"],
                ["say", "hi"],
                ["play", "up"],
                ["pat"],
                ["tease"],
                ["move", "--dx", "1", "--dy", "2"],
            ]
        )
        code, _ = _run(argv, capsys)
        assert code in (0, 4), f"control.json 脏文件 -> {code}"
        capsys.readouterr()


def test_monkey_cli_missing_subcommand_and_help(cli_paths, capsys):
    """空 argv / 仅全局 flag：argparse 用法错误退出码 2。"""
    for argv in ([], ["--json"], ["--unknown"]):
        with pytest.raises(SystemExit) as exc:
            cli_mod.main(argv)
        assert exc.value.code == 2
        capsys.readouterr()


def test_monkey_cli_validation_paths_exit_3(cli_paths):
    """不走控制服务即可判定的校验失败必须 exit 3。"""
    cases = [
        ["say", "text", "--mood", "up"],  # text 与 mood 二选一
        ["say"],  # 全空
        ["play", "loading"],  # 非表演状态
        ["move"],  # 缺坐标
        ["history", "--limit", "0"],
        ["alerts", "--limit", "-1"],
        ["alerts", "--kind", "bogus"],
        ["price", "--metal", "bogus"],
    ]
    for argv in cases:
        assert cli_mod.main(argv) == 3, f"argv={argv}"
