"""Monkey 测试 R5 目标 2：配置 API 往返守恒 + 范围不变量（前四轮未覆盖）。

种子：20261104。

角度：前四轮盯的是「加载容错」（读侧）与「写盘故障」（写侧），
没人验证 **load(set(sanitize(x))) 是否收敛**——即「脏输入 → 规范化 → 再存再读」
之后值是否还在合法域内、是否会漂移。这是配置系统最常见的慢性病：

  1. 幂等性：`sanitize(sanitize(x)) == sanitize(x)`（跑两遍不再变）；
  2. 往返性：save→load 后所有键仍在合法域（枚举成员 / 区间内 / bool 类型）；
  3. 类型纯净：任何键都不得在往返后变成「字符串数字」「None 冒充 0」这类带毒值；
  4. 未知键不落盘、已知键不丢失（key 集合守恒）；
  5. 区间不变量：interval_sec/opacity/care 间隔/alias 阈值 ∈ 契约范围。

oracle：sanitize 幂等；往返后 as_dict() 与契约表逐键比对；不抛异常。
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from _monkey_common import dirty_value

from gold_pet.config import (
    ACTIVITY_LEVELS,
    CARE_INTERVAL_CHOICES,
    DEFAULTS,
    INTERVAL_CHOICES,
    METAL_NAMES,
    SPARK_RANGE_NAMES,
    UNIT_NAMES,
    Config,
)

SEED = 20261104
ITERATIONS = 300

BOOL_KEYS = (
    "always_on_top",
    "show_bubble",
    "show_spark",
    "autostart",
    "alert_pct_enabled",
    "alert_target_enabled",
    "alert_extreme_enabled",
    "dnd_enabled",
    "tray_show_price",
    "control_enabled",
    "care_drink_enabled",
    "care_posture_enabled",
    "care_eye_enabled",
)


# 每键的合法域断言器
def _assert_domain(cfg: Config) -> None:
    d = cfg.as_dict()
    assert d["metal"] in METAL_NAMES
    assert d["unit"] in UNIT_NAMES
    assert d["interval_sec"] in INTERVAL_CHOICES
    assert 0.0 <= d["alert_pct"] <= 20.0
    assert 60 <= d["alert_cooldown_sec"] <= 7200
    assert 0.0 <= d["alert_extreme_step_pct"] <= 5.0
    assert 0.3 <= d["opacity"] <= 1.0
    assert d["pos"] is None or (
        isinstance(d["pos"], list)
        and len(d["pos"]) == 2
        and all(isinstance(v, int) for v in d["pos"])
    )
    for k in ("target_high", "target_low"):
        assert isinstance(d[k], float) and d[k] >= 0.0
    for k in BOOL_KEYS:
        assert isinstance(d[k], bool), (k, d[k])
    assert d["spark_range"] in SPARK_RANGE_NAMES
    assert d["activity_level"] in ACTIVITY_LEVELS
    for k in ("care_drink_interval_min", "care_posture_interval_min", "care_eye_interval_min"):
        assert d[k] in CARE_INTERVAL_CHOICES, (k, d[k])
    for k in ("dnd_start", "dnd_end"):
        text = d[k]
        assert isinstance(text, str) and len(text.split(":")) == 2


def _jsonable(text: str) -> bool:
    """孤代理对等无法以 UTF-8 编码的字符串无法落盘，随机用例里跳过。"""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _dump(raw: dict) -> str | None:
    """把随机脏 dict 序列化为可落盘 JSON；不可行时返回 None。"""
    try:
        text = json.dumps(raw, ensure_ascii=False)
        text.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        return None
    return text


# ------------------------------------------------------------------ 定向：往返守恒
def test_r5_rt_defaults_roundtrip(tmp_path):
    """默认配置往返：逐键等于 DEFAULTS，且 key 集合守恒。"""
    path = tmp_path / "settings.json"
    cfg = Config.load(path)
    cfg.save()
    again = Config.load(path)
    assert again.as_dict() == cfg.as_dict()
    assert set(cfg.as_dict()) == set(DEFAULTS)


def test_r5_rt_unknown_keys_dropped(tmp_path):
    """未知键（手改文件加进来的）不得进入内存，也不得被写回磁盘。"""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"metal": "gold", "__evil__": 1, "": 2, "null": 3}), encoding="utf-8"
    )
    cfg = Config.load(path)
    assert "__evil__" not in cfg.as_dict()
    cfg.save()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert set(on_disk) == set(DEFAULTS)
    assert "__evil__" not in on_disk


def test_r5_rt_huge_numbers_overflow(tmp_path):
    """R2-M-01 同族：JSON 任意精度整数（10**400）往返不炸、不残留 inf/nan。"""
    path = tmp_path / "settings.json"
    path.write_text(
        '{"alert_pct": '
        + "9" * 400
        + ', "opacity": 1e999, "interval_sec": '
        + "8" * 400
        + ', "alert_cooldown_sec": -'
        + "7" * 400
        + "}",
        encoding="utf-8",
    )
    cfg = Config.load(path)
    _assert_domain(cfg)
    cfg.save()
    text = path.read_text(encoding="utf-8")
    json.loads(text, parse_constant=lambda n: (_ for _ in ()).throw(AssertionError(n)))
    _assert_domain(Config.load(path))


# ------------------------------------------------------------------ 幂等
@pytest.mark.parametrize("rounds", [1, 2, 3])
def test_r5_rt_sanitize_idempotent_random(rounds):
    """随机脏配置：sanitize 幂等——跑 N 遍结果一致。"""
    rnd = random.Random(SEED + rounds)
    for _ in range(ITERATIONS):
        raw = {k: dirty_value(rnd) for k in DEFAULTS if rnd.random() < 0.9}
        c1 = Config(raw)
        c1.sanitize()
        first = c1.as_dict()
        for _r in range(rounds):
            c2 = Config(first)
            c2.sanitize()
            assert c2.as_dict() == first, "sanitize 不幂等"
        _assert_domain(c1)


def test_r5_rt_random_dirty_reload_convergence(tmp_path):
    """脏配置 → 规范化 → 落盘 → 重载 = 不动点（第三遍与第二遍逐键一致）。"""
    rnd = random.Random(SEED + 7)
    path = tmp_path / "settings.json"
    for i in range(60):
        raw = {k: dirty_value(rnd) for k in DEFAULTS if rnd.random() < 0.8}
        text = _dump(raw)
        if text is None:
            continue
        path.write_text(text, encoding="utf-8")
        c1 = Config.load(path)
        c1.save()
        c2 = Config.load(path)
        c2.save()
        c3 = Config.load(path)
        assert c1.as_dict() == c2.as_dict() == c3.as_dict(), f"第 {i} 轮不收敛"
        _assert_domain(c3)


# ------------------------------------------------------------------ 边界值矩阵
@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("interval_sec", 15, 15),
        ("interval_sec", 16, 60),  # 不在档位 -> 回落默认
        ("interval_sec", 14.9, 60),  # int(14.9)=14 不在档位 -> 默认
        ("interval_sec", "15", 15),  # 字符串可转 int
        ("interval_sec", 15.7, 15),  # int(15.7)=15 命中档位（_as_int 先转 float 再 int）
        ("opacity", 0.29, 0.3),
        ("opacity", 0.3, 0.3),
        ("opacity", 1.0, 1.0),
        ("opacity", 1.01, 1.0),
        ("alert_pct", -1, 0.0),
        ("alert_pct", 21, 20.0),
        ("alert_cooldown_sec", 10, 60),
        ("alert_cooldown_sec", 99999, 7200),
        ("alert_extreme_step_pct", 99, 5.0),
        ("target_high", -5, 0.0),
        ("target_low", 0, 0.0),
        ("activity_level", "quiet", "quiet"),
        ("activity_level", "QUIET", "normal"),
        ("spark_range", "all", "all"),
        ("spark_range", ["1h"], "1h"),
        ("metal", "silver", "silver"),
        ("metal", {"x": 1}, "gold"),
        ("unit", "usd_oz", "usd_oz"),
        ("dnd_start", "00:00", "00:00"),
        ("dnd_start", "24:00", DEFAULTS["dnd_start"]),
        ("dnd_start", "8:00", "8:00"),  # 单位数小时仍合法（parse 接受）
        ("care_eye_interval_min", 45, 45),
        ("care_eye_interval_min", 44, DEFAULTS["care_eye_interval_min"]),
    ],
)
def test_r5_rt_boundary_matrix(tmp_path, key, value, expected):
    cfg = Config({key: value})
    cfg.sanitize()
    assert cfg.get(key) == expected, f"{key}={value!r} -> {cfg.get(key)!r}，期望 {expected!r}"
    _assert_domain(cfg)


def test_r5_rt_pos_matrix():
    """pos 只接受「长度 2 的 int 列表」：其它一律 None，bool 不算 int 的坑要挡住。"""
    ok = [[0, 0], [-100, 500], [3000, 2000]]
    for value in ok:
        cfg = Config({"pos": value})
        cfg.sanitize()
        assert cfg.get("pos") == value
    bad = [
        None,
        [],
        [1],
        [1, 2, 3],
        ["1", "2"],
        [1.0, 2.0],
        [True, False],  # R5-M-02：bool 是 int 子类，既有 sanitize 放行（已记 Issue）
        {"x": 1, "y": 2},
        "1,2",
    ]
    for value in bad:
        if value == [True, False]:
            # R5-M-02 待修：修复后这里应变 None；当前以 xfail 语义跳过严格断言
            cfg = Config({"pos": value})
            cfg.sanitize()
            assert cfg.get("pos") is not None, "R5-M-02 已修复：请把该用例移回 bad 严格分支"
            continue
        cfg = Config({"pos": value})
        cfg.sanitize()
        assert cfg.get("pos") is None, value


def test_r5_rt_bool_written_as_number_or_string(tmp_path):
    """布尔项被手写成 0/1/"yes" 等：必须收敛为 bool 类型（不是 int/str）。

    口径以 REVIEW5-02 的 `_as_bool` 为准：bool 原样、int/float 按数值、
    字符串认常见真/假写法（大小写与空白不敏感），**未知字符串与容器（list/dict）
    一类回落该键默认值**——不再沿用 `bool()` 的 truthiness（那会让 "false" 变 True）。
    """
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "show_bubble": 0,
                "show_spark": 1,
                "autostart": "yes",
                "dnd_enabled": "false",
                "tray_show_price": [],
                "control_enabled": {"a": 1},
            }
        ),
        encoding="utf-8",
    )
    cfg = Config.load(path)
    _assert_domain(cfg)
    assert cfg.get("show_bubble") is False  # 数值 0 -> False
    assert cfg.get("show_spark") is True  # 数值 1 -> True
    assert cfg.get("autostart") is True  # "yes" 在白名单 -> True
    assert cfg.get("dnd_enabled") is False  # "false" -> False（不再被反转）
    # 容器不属于「真/假写法」，回落各键默认值（tray_show_price / control_enabled 默认均 True）
    assert cfg.get("tray_show_price") is DEFAULTS["tray_show_price"]
    assert cfg.get("control_enabled") is DEFAULTS["control_enabled"]


# ------------------------------------------------------------------ 范围不变量（随机）
def test_r5_rt_invariants_random_burst(tmp_path):
    """300 轮随机脏输入：每轮都满足契约域，且落盘文本恒为合法 JSON。"""
    rnd = random.Random(SEED + 11)
    path = tmp_path / "settings.json"
    for _ in range(ITERATIONS):
        raw = {k: dirty_value(rnd, max_depth=4) for k in DEFAULTS if rnd.random() < 0.9}
        text = _dump(raw)
        if text is None:
            continue  # 不可 JSON 化 / 不可 UTF-8 编码的脏值：文件构造不出来，跳过
        path.write_text(text, encoding="utf-8")
        cfg = Config.load(path)
        cfg.save()
        written = path.read_text(encoding="utf-8")
        json.loads(written, parse_constant=lambda n: (_ for _ in ()).throw(AssertionError(n)))
        _assert_domain(Config.load(path))


def test_r5_rt_config_save_to_dir_path_swallowed(tmp_path):
    """save 目标是目录：OSError 被吞（R3-M-03），不抛给调用方。"""
    cfg = Config.load(tmp_path / "settings.json")
    d = tmp_path / "isdir"
    d.mkdir()
    cfg._path = d  # type: ignore[attr-defined]
    cfg.save()  # 不得抛
    assert d.is_dir()
