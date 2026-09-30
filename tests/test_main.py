"""入口（main.py）与开机自启（autostart.py）的测试。

main.py 原本覆盖率为 0，这里覆盖参数解析、--once 输出与错误分支；
autostart 在非 Windows 上的空实现也在本文件覆盖（Windows 下等价于开关往返）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gold_pet import __version__, autostart  # noqa: E402
from gold_pet import main as main_mod  # noqa: E402
from gold_pet.config import Config  # noqa: E402
from gold_pet.price import Snapshot, parse_snapshot  # noqa: E402

RAW = {
    "gds_AUTD": (
        "897.15,0,896.03,897.25,902.00,889.24,15:29:54,900.37,897.00,45270,2,7,2026-09-29,黄金延期"
    ).split(","),
    "hf_XAU": (
        "4138.36,4114.93,4138.36,4138.71,4148.85,4113.30,16:37:00,4114.93,4117.01,0,0,0,"
        "2026-09-29,伦敦金"
    ).split(","),
    "gds_AGTD": (
        "14801.00,0,14795,14801,15010,14692,15:30:00,14850,14890,341344,1,19,2026-09-29,白银延期"
    ).split(","),
    "hf_XAG": (
        "60.60,60.62,60.60,60.64,61.00,60.28,16:37:00,60.62,60.62,0,0,0,2026-09-29,伦敦银"
    ).split(","),
    "fx_susdcny": (
        "16:37:00,6.7038,6.7042,6.7118,61,6.7094,6.7094,6.7033,6.7042,x,-0.11,0,0,y,0,0,,2026-09-29"
    ).split(","),
}

# ------------------------------------------------------------------ 参数解析


def test_parse_args_defaults():
    args = main_mod.parse_args([])
    assert args.once is False
    assert args.metal is None
    assert args.unit is None
    assert args.log_level == "INFO"


def test_parse_args_full():
    args = main_mod.parse_args(
        ["--once", "--metal", "silver", "--unit", "usd_oz", "--log-level", "DEBUG"]
    )
    assert args.once is True
    assert args.metal == "silver"
    assert args.unit == "usd_oz"
    assert args.log_level == "DEBUG"


def test_parse_args_rejects_bad_metal():
    with pytest.raises(SystemExit):
        main_mod.parse_args(["--metal", "不存在的品种"])


def test_parse_args_rejects_bad_unit():
    with pytest.raises(SystemExit):
        main_mod.parse_args(["--unit", "kg"])


def test_parse_args_version_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        main_mod.parse_args(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_version_format():
    """版本号应符合 x.y.z 形式，便于与 tag / 文档对齐。"""
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_ensure_utf8_stdout_is_safe():
    """编码重设不应抛异常，且重设后 stdout 仍可正常写入中文。"""
    main_mod._ensure_utf8_stdout()
    main_mod._ensure_utf8_stdout()  # 幂等
    import sys as _sys

    # 只要没抛异常即可；打印中文验证不会 UnicodeEncodeError
    print("黄金桌宠 编码自检")
    assert _sys.stdout is not None


# ------------------------------------------------------------------ run_once


class _StubService:
    """替身：返回预置的 Snapshot，不联网。"""

    def __init__(self, snap: Snapshot):
        self._snap = snap

    def fetch(self, timeout: float = 8.0) -> Snapshot:  # noqa: ARG002
        return self._snap


def test_run_once_prints_quote(monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "PriceService", lambda _p: _StubService(parse_snapshot(RAW)))
    args = main_mod.parse_args(["--once"])
    code = main_mod.run_once(args, Config())
    out = capsys.readouterr().out
    assert code == 0
    assert "897.15" in out
    assert "黄金" in out
    assert "▼" in out  # 897.15 < 昨收 900.37


def test_run_once_honours_cli_overrides(monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "PriceService", lambda _p: _StubService(parse_snapshot(RAW)))
    args = main_mod.parse_args(["--once", "--metal", "silver", "--unit", "usd_oz"])
    assert main_mod.run_once(args, Config()) == 0
    out = capsys.readouterr().out
    assert "白银" in out
    assert "60.60" in out


def test_run_once_returns_1_on_failure(monkeypatch, capsys):
    empty = Snapshot(ok=False, error="网络不可用")
    monkeypatch.setattr(main_mod, "PriceService", lambda _p: _StubService(empty))
    args = main_mod.parse_args(["--once"])
    assert main_mod.run_once(args, Config()) == 1
    assert "网络不可用" in capsys.readouterr().out


# ------------------------------------------------------------------ autostart


def test_autostart_is_enabled_returns_bool():
    assert isinstance(autostart.is_enabled(), bool)


def test_autostart_set_enabled_returns_bool():
    assert isinstance(autostart.set_enabled(False), bool)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 下为真实注册表开关")
def test_autostart_noop_on_non_windows():
    """非 Windows 平台所有操作都是安全空操作。"""
    assert autostart.is_enabled() is False
    assert autostart.set_enabled(True) is False


def test_autostart_command_shape(monkeypatch):
    """开机启动命令应是一个被引号包裹的可执行路径。"""
    cmd = autostart._command()
    assert cmd.startswith('"')
    assert cmd.rstrip().endswith('"') or '"' in cmd[1:]


def test_autostart_roundtrip_on_windows():
    """Windows 下开关自启应能往返（失败时返回 False，不抛异常）。"""
    if sys.platform != "win32":
        pytest.skip("仅 Windows")
    original = autostart.is_enabled()
    try:
        assert autostart.set_enabled(True) is True
        assert autostart.is_enabled() is True
    finally:
        autostart.set_enabled(original)
    assert autostart.is_enabled() is original
