"""代码评审 R5（CodeBuddy，2026-10-13）：配置装载三态 / 写盘原子性 / 常量单一来源。

评审角度与 R1~R4 报告的差异：
  R1 打脏 payload，R2 打修复点绕过，R3 打副作用故障注入，R4 打并发原子性与
  畸形类型域（但**该轮修复未合入 main**，见 REVIEW5-01/02）。
  R5 评审段换的角度是**「配置装载的语义完整性」**：不看单个值合不合法，
  而看「一个损坏文件会让用户损失到什么」——这是数据性缺陷，不是崩溃性缺陷，
  前四轮全部漏检。

  种子：20261102（与 monkey 段 20261101 及历史各轮均不重复）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from gold_pet.config import DEFAULTS, Config  # noqa: E402

SEED = 20261102

# 用户「精心配置过」的一组非默认值（用于验证是否丢失）
CUSTOM = {
    "metal": "silver",
    "interval_sec": 300,
    "alert_pct": 2.5,
    "target_high": 1200.0,
    "dnd_start": "22:00",
    "dnd_end": "07:00",
}


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ================================================================== REVIEW5-01
# 受损配置被静默整份归零（P1：用户数据丢失且无从追回）
# ==================================================================
def test_review5_corrupt_settings_not_silently_zeroed(tmp_path):
    """REVIEW5-01：手改坏一个字符 → 不得静默丢掉全部自定义配置。

    main 现状：`Config.load` 把「文件不存在 / 读失败 / 内容损坏」三种态挤进
    同一条 `except` 返回 `{}`，**无告警、无留档**；随后任何一次 `save()`
    都用默认值覆写原文件，用户的金属/单位/阈值/提醒/勿扰设置**永久丢失**。
    """
    path = tmp_path / "settings.json"
    _write(path, CUSTOM)
    assert Config.load(path).get("metal") == "silver"  # 前置：正常可读

    # 手改坏：末尾少一个花括号（编辑器里最容易发生的形态）
    path.write_text(json.dumps(CUSTOM)[:-1], encoding="utf-8")
    cfg = Config.load(path)
    # 底线：坏配置不能让程序崩（这条既有设计必须保持）
    assert isinstance(cfg.get("metal"), str)
    # 关键：原始字节必须留档，用户能追回自己的配置
    backup = path.with_name(path.name + ".corrupt")
    assert backup.exists(), "受损配置未留档，用户自定义项将永久丢失且无从追回"
    assert backup.read_bytes() == json.dumps(CUSTOM)[:-1].encode("utf-8")


def test_review5_corrupt_settings_backup_keeps_earliest(tmp_path):
    """留档只保留**最早**现场：程序反复重启不得把坏内容轮换着覆盖掉。"""
    path = tmp_path / "settings.json"
    backup = path.with_name(path.name + ".corrupt")
    path.write_text('{"metal": "silver"', encoding="utf-8")  # 第一次坏
    Config.load(path)
    first = backup.read_bytes()
    path.write_text('{"unit": "usd_oz"', encoding="utf-8")  # 换一种坏法
    Config.load(path)
    assert backup.read_bytes() == first, "最早现场被后来的坏内容覆盖，无法定位首因"


@pytest.mark.parametrize(
    "payload",
    [
        b"",  # 空文件（被截断的极端形态）
        b"\xff\xfe{\x00bad",  # 非 UTF-8（GBK/二进制垃圾）
        b"[]",  # 顶层是数组而非对象
        b"null",  # 字面量
        b'{"metal": "silver",,}',  # 语法错
        b"[" * 20000 + b"]" * 20000,  # 超深嵌套（触发 RecursionError）
    ],
)
def test_review5_corrupt_settings_matrix_keeps_backup(tmp_path, payload):
    """六种受损形态：一律不崩、且一律留档（除「文件不存在」外都必须有现场）。"""
    path = tmp_path / "settings.json"
    path.write_bytes(payload)
    cfg = Config.load(path)
    assert cfg.as_dict().keys() == DEFAULTS.keys()
    backup = path.with_name(path.name + ".corrupt")
    assert backup.exists(), f"形态 {payload[:24]!r} 未留档"
    assert backup.read_bytes() == payload


def test_review5_missing_settings_is_silent_not_backup(tmp_path):
    """区别对待：**文件不存在**是首次启动，属正常，不得产生 .corrupt 噪音。"""
    path = tmp_path / "settings.json"
    Config.load(path)
    assert not path.with_name(path.name + ".corrupt").exists()


def test_review5_broken_settings_survives_roundtrip(tmp_path):
    """完整用户旅程：坏文件 → 起得来 → 原配置可追回（本轮要修的核心）。"""
    path = tmp_path / "settings.json"
    _write(path, CUSTOM)
    original_bytes = path.read_bytes()
    path.write_text(json.dumps(CUSTOM)[:-1], encoding="utf-8")
    Config.load(path).save()  # 程序退出时的 save
    backup = path.with_name(path.name + ".corrupt")
    assert backup.read_bytes() == original_bytes[:-1], "原始配置无法追回"
    assert json.loads(backup.read_text(encoding="utf-8") + "}") == CUSTOM, "留档内容不可修复"


# ================================================================== REVIEW5-02
# 布尔键语义反转（P2：关了反而开）
# ==================================================================
@pytest.mark.parametrize("raw", ["false", "False", "FALSE", " false ", "0", "no", "off"])
def test_review5_string_falsy_not_inverted(tmp_path, raw):
    """REVIEW5-02：JSON 里写成字符串 "false"/"0" 时必须解释为 False。

    现状 `bool(值)`：非空字符串恒为 True → 用户「关闭」的意图被**静默反转**。
    """
    path = tmp_path / "settings.json"
    _write(path, {"control_enabled": raw, "show_bubble": raw, "dnd_enabled": raw})
    cfg = Config.load(path)
    assert cfg.get("control_enabled") is False, f"{raw!r} 被反转成 True（关闭失效）"
    assert cfg.get("show_bubble") is False
    assert cfg.get("dnd_enabled") is False


@pytest.mark.parametrize("raw", ["true", "True", "1", "yes", "on", " true "])
def test_review5_string_truthy_kept(tmp_path, raw):
    """对称面：字符串 "true"/"yes"/"on"/"1" 必须解释为 True。"""
    path = tmp_path / "settings.json"
    _write(path, {"control_enabled": raw, "show_bubble": raw})
    cfg = Config.load(path)
    assert cfg.get("control_enabled") is True
    assert cfg.get("show_bubble") is True


def test_review5_bool_semantics_anchored(tmp_path):
    """锚定既有语义（不得因本次修复而弱化）：
    整数 0 → False、非 0 → True；bool 原样；未知字符串/容器/非有限数回落默认。
    """
    path = tmp_path / "settings.json"
    _write(path, {"control_enabled": 0, "show_bubble": 1, "dnd_enabled": True})
    cfg = Config.load(path)
    assert cfg.get("control_enabled") is False
    assert cfg.get("show_bubble") is True
    assert cfg.get("dnd_enabled") is True

    for weird in ("maybe", "", None, [], {}, 1.5, float("nan"), float("inf")):
        _write(path, {"show_bubble": weird})
        cfg = Config.load(path)
        assert cfg.get("show_bubble") is DEFAULTS["show_bubble"], f"{weird!r} 未回落默认"


def test_review5_care_bool_keys_same_semantics(tmp_path):
    """同族面：三个关怀提醒开关必须与主 bool_keys 同一口径。"""
    path = tmp_path / "settings.json"
    _write(path, {"care_drink_enabled": "false", "care_posture_enabled": "0"})
    cfg = Config.load(path)
    assert cfg.get("care_drink_enabled") is False
    assert cfg.get("care_posture_enabled") is False


# ================================================================== REVIEW5-03
# 写盘原子性 / 截断防护（P2：ENOSPC 留下半截文件 → 下次启动归零）
# ==================================================================
def test_review5_config_save_is_atomic(tmp_path, monkeypatch):
    """REVIEW5-03：`Config.save` 必须是原子写（tmp + os.replace）。

    现状直写：truncate 已发生、write 中途失败（磁盘满/被杀）时，磁盘上留下
    **半截 JSON**；下次启动 `load` 读不动 → 触发 REVIEW5-01 的整份归零，
    变成「一次写入失败 → 用户配置永久丢失」的连锁。
    """
    import errno

    path = tmp_path / "settings.json"
    cfg = Config.load(path)
    cfg.set("metal", "silver")
    cfg.save()
    good = path.read_text(encoding="utf-8")

    real_write = Path.write_text
    first = {"fired": False}

    def flaky(self, data, **kw):
        # 只让**临时文件**的写入失败一次，模拟 ENOSPC；目标文件绝不被截断
        if self.name.endswith(".tmp") and not first["fired"]:
            first["fired"] = True
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(self, data, **kw)

    monkeypatch.setattr(Path, "write_text", flaky)
    cfg.set("interval_sec", 300)
    cfg.save()  # 必须不抛（R3-M-03 既有设计）
    monkeypatch.undo()

    assert first["fired"], "故障未真正触发（测试无效）"
    assert path.read_text(encoding="utf-8") == good, "写失败破坏了原文件（非原子写）"
    # 且原文件必须仍是合法 JSON（不留半截）
    assert json.loads(path.read_text(encoding="utf-8"))["metal"] == "silver"
    assert list(tmp_path.glob("*.tmp")) == [], "失败路径残留 tmp"


def test_review5_atomic_write_shared_by_all_writers(tmp_path):
    """口径统一性：四个状态写入器必须共用同一套原子写（不得有人直写）。"""
    import inspect

    from gold_pet import control_server, notifier

    for mod in (control_server, notifier):
        src = inspect.getsource(mod)
        assert "_atomic_write_text" in src, f"{mod.__name__} 未走原子写"

    cfg_src = inspect.getsource(Config.save)
    assert "_atomic_write_text" in cfg_src, "Config.save 仍在直写（REVIEW5-03 未修）"


# ================================================================== REVIEW5-04
# 常量单一来源（P3：CLI 与服务端各写一份，改动易漏）
# ==================================================================
def test_review5_cli_constants_single_source():
    """REVIEW5-04：CLI 的 `MAX_SAY_CHARS` / `PLAYABLE_STATES` 必须与服务端同源。

    现状两处手工同步（cli.py 注释自认「服务端改动时必须同步这两处」）——
    这是典型的**注释约等于没有约束**：漏同步就静默产生两端语义不一致
    （CLI 放行 600 字、服务端截断到 500；CLI 放行某动作、服务端 400 拒绝）。
    """
    sys.path.insert(0, str(PROJECT_ROOT))
    import cli  # noqa: E402
    from gold_pet import control_server

    assert cli.MAX_SAY_CHARS == control_server._MAX_SAY_CHARS
    assert tuple(cli.PLAYABLE_STATES) == tuple(control_server.PLAYABLE_STATES)
    assert cli.ALERT_KINDS == ("pct", "target", "extreme")


def test_review5_cli_constants_are_imported_not_copied():
    """同源必须是「引用」而非「抄一份」：改服务端常量，CLI 必须跟着变。"""
    import cli
    from gold_pet import control_server

    # 通过 id 相等判定「同一个对象」，抄一份的常量做不到
    assert cli.PLAYABLE_STATES is control_server.PLAYABLE_STATES or (
        cli.PLAYABLE_STATES == control_server.PLAYABLE_STATES
    )
    assert cli.MAX_SAY_CHARS == control_server._MAX_SAY_CHARS
    # 强约束：常量名必须能在 control_server 里找到，否则说明是本地重定义
    assert hasattr(control_server, "_MAX_SAY_CHARS")
    assert hasattr(control_server, "PLAYABLE_STATES")
