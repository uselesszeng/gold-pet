"""Monkey 测试 R3 目标 4：写入中断恢复（半截 JSON / 半截 tmp / rename 前后 kill）。

种子：20261024。
场景构造（模拟进程被强杀/断电）：
  1. 半截 JSON：按随机切点截断合法状态文件（含 UTF-8 多字节中间切断）；
  2. 半截 tmp：目标旁留下写了一半的 .tmp 文件（原子写 rename 之前被 kill）；
  3. rename 前后状态：tmp 完整 + 目标为旧数据（rename 未发生），加载器必须只认目标；
  4. 自愈：污染后重启加载不崩，下一次写入必须产出严格合法 JSON 并可再次加载。

覆盖文件：history.json / alert_history.json / last_price.json / settings.json / control.json。
oracle：加载器不抛异常、返回结构合法；写后文件严格 JSON（拒绝 NaN/Infinity 字面量）；
目标文件绝不会被 .tmp 内容顶替；单轮 ≤2s。
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from _monkey_common import assert_valid_json_text

from gold_pet import control_server as cs
from gold_pet.config import Config
from gold_pet.notifier import Alert, Notifier
from gold_pet.price import HistoryStore, PriceService, Snapshot

SEED = 20261024
ITERATIONS = 500

KINDS = ("history", "alerts", "cache", "settings", "control")

GOOD_HISTORY = json.dumps({"gold|cny_g": [{"t": 1700000000.0, "p": 900.0}]}, ensure_ascii=False)
GOOD_ALERTS = json.dumps(
    [{"kind": "pct", "side": "up", "title": "t", "body": "b", "at": 1700000000.0}],
    ensure_ascii=False,
)
GOOD_CACHE = json.dumps(
    {
        "quotes": {"gold|cny_g": {"code": "x", "name": "黄金", "price": 900.0,
                                  "prev_close": 899.0, "high": 901.0, "low": 898.0,
                                  "time_text": "", "unit": "元/克", "currency": "CNY",
                                  "converted": False}},
        "fx": 6.7, "updated_at": 1700000000.0, "ok": True, "error": "",
    },
    ensure_ascii=False,
)
GOOD_SETTINGS = json.dumps({"metal": "silver", "opacity": 0.8}, ensure_ascii=False)
GOOD_CONTROL = json.dumps({"port": 45999, "token": "tok", "pid": os.getpid()}, ensure_ascii=False)

GOOD = {
    "history": GOOD_HISTORY,
    "alerts": GOOD_ALERTS,
    "cache": GOOD_CACHE,
    "settings": GOOD_SETTINGS,
    "control": GOOD_CONTROL,
}


def _filename(kind: str) -> str:
    return {
        "history": "history.json",
        "alerts": "alert_history.json",
        "cache": "last_price.json",
        "settings": "settings.json",
        "control": "control.json",
    }[kind]


def _load(kind: str, path: Path, monkeypatch=None):
    """按 kind 走生产加载器，返回结果（不得抛异常）。"""
    if kind == "history":
        return HistoryStore(path).load()
    if kind == "alerts":
        return Notifier(Config(), path).history()
    if kind == "cache":
        return PriceService(path).load_cache()
    if kind == "settings":
        return Config.load(path)
    if kind == "control":
        assert monkeypatch is not None
        monkeypatch.setattr(cs, "control_info_path", lambda: path)
        return cs.read_control_info()
    raise AssertionError(kind)


def _repair_write(kind: str, path: Path, monkeypatch=None) -> None:
    """模拟重启后的第一次正常写入（自愈）并断言产物严格 JSON。"""
    if kind == "history":
        HistoryStore(path).append("gold", "cny_g", 901.0)
    elif kind == "alerts":
        Notifier(Config(), path).record(
            [Alert(kind="pct", side="up", title="t", body="b", at=1700000100.0)]
        )
    elif kind == "cache":
        PriceService(path).save_cache(
            Snapshot(quotes={}, fx=6.7, updated_at=1700000000.0, ok=True, error="")
        )
    elif kind == "settings":
        cfg = Config.load(path)
        cfg.set("metal", "gold")
        cfg.save()
    elif kind == "control":
        return  # 只读文件，无写入器
    assert_valid_json_text(path.read_text(encoding="utf-8"))


def _corrupt(text: str, mode: str, rnd: random.Random) -> str:
    if mode == "truncate":
        cut = rnd.randint(0, max(0, len(text) - 1))
        return text[:cut]
    if mode == "truncate_utf8":
        raw = text.encode("utf-8")
        cut = rnd.randint(1, len(raw) - 1)
        return raw[:cut].decode("utf-8", errors="ignore")
    if mode == "garbage_tail":
        return text + rnd.choice(["]}", ",,,", "\x00\x01", '"unclosed'])
    if mode == "empty":
        return ""
    if mode == "bom_garbage":
        return "\ufeff" + text[: max(1, len(text) // 2)]
    if mode == "wrong_type":
        return rnd.choice(["[1,2,3]", '"just a string"', "12345", "null", "true"])
    if mode == "nan_literal":
        return '{"gold|cny_g": [{"t": NaN, "p": Infinity}]}'
    if mode == "deep":
        return "[" * 20000 + "]" * 20000
    raise AssertionError(mode)


CORRUPTION_MODES = (
    "truncate", "truncate_utf8", "garbage_tail", "empty", "bom_garbage",
    "wrong_type", "nan_literal", "deep",
)


# ------------------------------------------------------------------ 定向矩阵
@pytest.mark.parametrize("kind", KINDS)
def test_r3_interrupt_half_json_all_loaders(tmp_path, monkeypatch, kind):
    """每个状态文件的半截/坏结构文件：加载不崩、类型合法、随后自愈写盘。"""
    path = tmp_path / _filename(kind)
    rnd = random.Random(SEED + KINDS.index(kind))  # 固定种子（不用 hash()，避免进程随机化）
    for mode in CORRUPTION_MODES:
        path.write_text(_corrupt(GOOD[kind], mode, rnd), encoding="utf-8")
        result = _load(kind, path, monkeypatch)
        if kind == "history":
            assert isinstance(result, dict)
        elif kind == "alerts":
            assert isinstance(result, list)
        elif kind == "cache":
            assert result is None or isinstance(result, Snapshot)
        elif kind == "settings":
            assert isinstance(result, Config) and result.get("metal") in (
                "gold", "silver", "platinum")
        elif kind == "control":
            assert result is None or isinstance(result, dict)
        _repair_write(kind, path, monkeypatch)


@pytest.mark.parametrize("kind", KINDS)
def test_r3_interrupt_stale_tmp_never_consumed(tmp_path, monkeypatch, kind):
    """目标旁半截/完整 .tmp 文件：加载器绝不读取，写方仍写目标。"""
    path = tmp_path / _filename(kind)
    path.write_text(GOOD[kind], encoding="utf-8")
    tmp_leftover = tmp_path / f"{_filename(kind)}.1234.5678.9.tmp"
    leftover_text = GOOD[kind][: max(1, len(GOOD[kind]) // 2)]
    tmp_leftover.write_text(leftover_text, encoding="utf-8")

    result = _load(kind, path, monkeypatch)
    if kind == "settings":
        assert result.get("metal") == "silver"  # 读的是目标，不是半截 tmp
    if kind in ("history", "alerts", "cache"):
        assert result not in (None,)  # 结构化返回

    _repair_write(kind, path, monkeypatch)
    assert tmp_leftover.exists()  # 非本次写入的 tmp 不会被误删
    assert tmp_leftover.read_text(encoding="utf-8") == leftover_text  # 也不会被改写


@pytest.mark.parametrize("kind", ("history", "alerts", "cache", "settings"))
def test_r3_interrupt_rename_not_yet_done(tmp_path, monkeypatch, kind):
    """rename 未发生的中间态：完整新数据在 tmp、目标仍是旧数据 → 加载器只认旧目标。"""
    path = tmp_path / _filename(kind)
    old = GOOD[kind]
    path.write_text(old, encoding="utf-8")
    new = old.replace("1700000000.0", "1800000000.0").replace('"silver"', '"gold"')
    tmp_ready = tmp_path / f"{_filename(kind)}.42.42.42.tmp"
    tmp_ready.write_text(new, encoding="utf-8")

    _load(kind, path, monkeypatch)
    # 目标未被 tmp 顶替
    assert path.read_text(encoding="utf-8") == old
    # tmp 内容本身也是合法 JSON（原子写先写完整内容再 replace）
    assert_valid_json_text(tmp_ready.read_text(encoding="utf-8"))


def test_r3_interrupt_atomic_rename_sequence(tmp_path):
    """完整 rename 序列：tmp 写入→replace 后，目标 = 新内容、tmp 消失、严格 JSON。"""
    target = tmp_path / "history.json"
    target.write_text(GOOD_HISTORY, encoding="utf-8")
    store = HistoryStore(target)
    store.append("gold", "cny_g", 950.0)
    assert_valid_json_text(target.read_text(encoding="utf-8"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["history.json"]


# ------------------------------------------------------------------ 随机 500 轮
def test_r3_interrupt_random_kill_selfheal_burst(tmp_path, monkeypatch):
    """500 轮随机（文件 × 破坏模式 × 切点）：加载不崩 + 自愈写盘严格 JSON。"""
    rnd = random.Random(SEED)
    for i in range(ITERATIONS):
        kind = rnd.choice(KINDS)
        mode = rnd.choice(CORRUPTION_MODES)
        slot = tmp_path / f"case{i % 20}"
        slot.mkdir(exist_ok=True)
        path = slot / _filename(kind)
        path.write_text(_corrupt(GOOD[kind], mode, rnd), encoding="utf-8")
        try:
            _load(kind, path, monkeypatch)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"迭代 {i}: {kind}/{mode} 加载抛 {type(exc).__name__}: {exc}")
        _repair_write(kind, path, monkeypatch)
        # 再次加载：自愈后的文件必须能被同一加载器读回
        try:
            _load(kind, path, monkeypatch)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"迭代 {i}: {kind}/{mode} 自愈后重载抛 {type(exc).__name__}: {exc}")


def test_r3_interrupt_notifier_reload_after_corrupt(tmp_path):
    """alert_history 半截 → 新建 Notifier 首次加载不崩；record 后文件可再次加载。"""
    path = tmp_path / "alert_history.json"
    path.write_text(GOOD_ALERTS[: len(GOOD_ALERTS) // 2], encoding="utf-8")
    n1 = Notifier(Config(), path)
    assert n1.history() == []
    n1.record([Alert(kind="extreme", side="high", title="t", body="b", at=1700000200.0)])
    assert_valid_json_text(path.read_text(encoding="utf-8"))
    n2 = Notifier(Config(), path)
    assert len(n2.history()) == 1


def test_r3_interrupt_settings_reload_after_corrupt(tmp_path):
    """settings 半截 → 默认值；save 后文件严格 JSON 且可被再次加载。"""
    path = tmp_path / "settings.json"
    path.write_text(GOOD_SETTINGS[: len(GOOD_SETTINGS) // 2], encoding="utf-8")
    cfg = Config.load(path)
    assert cfg.get("metal") == "gold"  # 默认值
    cfg.save()
    assert_valid_json_text(path.read_text(encoding="utf-8"))
    cfg2 = Config.load(path)
    assert cfg2.get("metal") == "gold"
