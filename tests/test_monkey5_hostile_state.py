"""Monkey 测试 R5 目标 1：敌对状态文件（手改形态的「合法但致命」）

种子：20261101。

设计要点与 R1~R4 的差异：R1 灌脏 payload、R2 打修复绕过、R3 注入写盘/网络/线程故障、
R4 打并发原子性与跨进程。**R5 的新面是「文件内容形态」**——用户手改或工具写出的
状态文件里，那些能通过既有校验 `_valid_point` / `_safe_float`（它们只校验
「能否转成有限 float」），却在**下游按原生类型使用**时致命的形态：

  - `"t": "1700000000"`（数值字符串）→ `_valid_point` 放行，`HistoryStore.append`
    的 `now - series[-1].get("t", 0)` 抛 TypeError，击穿 UI 刷新槽；
  - `"kind": [1,2]`（不可哈希）→ `Alert.kind_name` 的 `KIND_NAMES.get()` 抛 TypeError，
    「提醒历史」窗口打开即崩；
  - `"at": 1e308`（超平台 time_t）→ `time.localtime()` 抛 OverflowError，
    提醒历史窗口 / 气泡渲染 / `spark_cutoff` / `evaluate` 全线崩。

oracle：任何形态的手改文件都不得让「刷新 → 渲染 → 提醒 → 历史窗口」链路抛异常；
状态文件读者要么看到旧内容要么看到新内容；落盘恒为严格 JSON。
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from gold_pet.config import Config  # noqa: E402
from gold_pet.notifier import Alert, Notifier, filter_series, spark_cutoff  # noqa: E402
from gold_pet.price import (  # noqa: E402
    HistoryStore,
    PriceService,
    Quote,
    Snapshot,
    _valid_point,
    key,
)
from gold_pet.ui import AlertHistoryDialog, PetWindow  # noqa: E402

SEED = 20261101
_RND = random.Random(SEED)

# 平台 time_t 的安全上界（Linux 64 位 int64 秒 ≈ 2^63-1，但年 1e12 已远超合理）
_SAFE_TS_HIGH = 32503680000.0  # 公元 3000 年，项目既有口径（price._num updated_at）


# ------------------------------------------------------------------ 语料
def _numeric_string_ts(rnd: random.Random) -> str:
    """能通过 `_valid_point` 的「数值字符串」时间戳形态（本轮核心语料）。"""
    return rnd.choice(
        [
            str(int(time.time())),
            str(int(time.time()) - rnd.randint(1, 86400)),
            f"{time.time():.6f}",
            f" {int(time.time())} ",
            f"{int(time.time())}e0",
        ]
    )


# 只放「能经 JSON 落盘」的形态：JSON 无法表达 set/frozenset/tuple
# （tuple 落盘后变 list），故语料取 list/dict —— 它们正是真实手改文件的形态。
UNHASHABLE_KINDS = [[1, 2], {"a": 1}, {"x": "y"}, [None], [[]], [{"deep": 1}]]
BAD_ATS = [1e308, -1e308, 1e30, -1e30, 2**63, -(2**63), "abc", None, [1], {"a": 1}]

HOSTILE_HISTORY_SERIES = [
    [{"t": "1700000000", "p": 900.0}],  # 数值字符串 t
    [{"t": "1700000000.5", "p": "900.5"}],  # 双字段字符串
    [{"t": f" {int(time.time())} ", "p": 900.0}],  # 带空白
    [{"t": time.time(), "p": 900.0}, {"t": str(int(time.time())), "p": 901.0}],  # 混排
    [{"t": -1e308, "p": 900.0}],
    [{"t": 0, "p": 900.0}],
]


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _window(tmp_path: Path, *, history: Path | None = None, **config) -> PetWindow:
    """构造真实 PetWindow（关控制服务，避免线程残留影响 pytest 收尾）。"""
    settings = tmp_path / "settings.json"
    _write(settings, {"control_enabled": False, **config})
    cfg = Config.load(settings)
    service = PriceService(tmp_path / "last_price.json")
    hist = HistoryStore(history or (tmp_path / "history.json"))
    return PetWindow(cfg, service, hist)


def _snap(price: float = 901.0, prev: float = 900.0) -> Snapshot:
    return Snapshot(
        quotes={
            key("gold", "cny_g"): Quote(
                code="gds_AUTD",
                name="黄金",
                price=price,
                prev_close=prev,
                high=price + 1.0,
                low=price - 1.0,
                unit="元/克",
                currency="CNY",
            )
        },
        fx=7.1,
        ok=True,
    )


# ================================================================== 目标 1-A
# HistoryStore：`_valid_point` 放行「数值字符串」但 append 按原生类型运算
# ==================================================================
@pytest.mark.parametrize("series", HOSTILE_HISTORY_SERIES)
def test_r5_history_append_survives_numeric_string_ts(tmp_path, series):
    """手改的「数值字符串 t」序列：append 不得抛（本轮 BUG-1）。"""
    path = tmp_path / "history.json"
    _write(path, {key("gold", "cny_g"): series})
    store = HistoryStore(path)
    assert store.load(), "前置条件：该形态必须能通过 _valid_point"
    result = store.append("gold", "cny_g", 902.0)  # 修复前 TypeError
    assert isinstance(result, list) and result
    # 落盘必须仍是严格 JSON 且点数有界
    text = path.read_text(encoding="utf-8")
    json.loads(text, parse_constant=lambda n: (_ for _ in ()).throw(AssertionError(n)))
    stored = json.loads(text)[key("gold", "cny_g")]
    assert len(stored) <= store.max_points


@pytest.mark.parametrize("series", HOSTILE_HISTORY_SERIES)
def test_r5_refresh_slot_survives_numeric_string_ts(tmp_path, series):
    """端到端：真实 PetWindow 刷新槽遇到该形态历史文件不得崩（本轮 BUG-1 证据面）。"""
    path = tmp_path / "history.json"
    _write(path, {key("gold", "cny_g"): series})
    w = _window(tmp_path, history=path, show_spark=True)
    try:
        w._render(_snap())  # → _update_spark(append=True) → HistoryStore.append
        assert w.spark.values, "走势图应有数据点"
    finally:
        w.control = None
        w.deleteLater()


def test_r5_history_append_numeric_string_ts_burst(tmp_path):
    """500 次随机「数值字符串 t」写入轰炸：append 恒不抛、文件恒严格 JSON。"""
    path = tmp_path / "history.json"
    store = HistoryStore(path)
    bad = 0
    for i in range(500):
        if _RND.random() < 0.5:
            _write(
                path,
                {
                    key("gold", "cny_g"): [
                        {"t": _numeric_string_ts(_RND), "p": 900.0 + i % 7},
                        {"t": _numeric_string_ts(_RND), "p": "900.5"},
                    ]
                },
            )
        try:
            store.append("gold", "cny_g", 900.0 + i)
        except Exception as exc:  # noqa: BLE001 - 探测型用例，任何异常都算缺陷
            bad += 1
            pytest.fail(f"第 {i} 次 append 抛出 {type(exc).__name__}: {exc}")
    text = path.read_text(encoding="utf-8")
    json.loads(text, parse_constant=lambda n: (_ for _ in ()).throw(AssertionError(n)))
    assert bad == 0


def test_r5_valid_point_oracle_unchanged():
    """oracle 锚定：`_valid_point` 对数值字符串仍是「合法」（本轮不改变既有语义）。"""
    assert _valid_point({"t": "1700000000", "p": "900.5"}) is True
    # 字符串 t 正是本轮 BUG-1 的入口形态：校验层放行、使用层致命——故此处锚定
    # 「放行」这一既有语义不变，修复只发生在使用层（不收紧校验，避免动到既有契约）。
    assert _valid_point({"t": " 1700000000 ", "p": 900.0}) is True
    # t 为 None 属既有「坏点」判定，维持不变
    assert _valid_point({"t": None, "p": 900.0}) is False


# ================================================================== 目标 1-B
# Alert.kind_name：不可哈希 kind
# ==================================================================
@pytest.mark.parametrize("kind", UNHASHABLE_KINDS)
def test_r5_alert_kind_name_survives_unhashable(tmp_path, kind):
    """不可哈希 kind 的提醒记录：kind_name 不得抛（本轮 BUG-2）。"""
    alert = Alert.from_dict({"kind": kind, "side": "up", "title": "t", "body": "b", "at": 1.0})
    name = alert.kind_name  # 修复前 TypeError: unhashable type
    assert isinstance(name, str)


@pytest.mark.parametrize("kind", UNHASHABLE_KINDS)
def test_r5_alert_history_dialog_survives_unhashable(tmp_path, kind):
    """端到端：坏 alert_history.json 不得让「提醒历史」窗口打开即崩（BUG-2 证据面）。"""
    path = tmp_path / "alert_history.json"
    _write(path, [{"kind": kind, "side": "up", "title": "t", "body": "b", "at": 1.0}])
    notifier = Notifier(Config(), path)
    dlg = AlertHistoryDialog(notifier)
    try:
        assert dlg.list.count() >= 1
    finally:
        dlg.deleteLater()


def test_r5_alert_history_kind_unhashable_burst(tmp_path):
    """500 条随机脏 kind 的提醒历史：窗口构建恒不抛，条目数 == 有效记录数。"""
    path = tmp_path / "alert_history.json"
    items = []
    for i in range(500):
        items.append(
            {
                "kind": _RND.choice(UNHASHABLE_KINDS + ["pct", "target", "extreme", "", None, 0]),
                "side": "up",
                "title": "t",
                "body": "b",
                "at": 1700000000.0 + i,
            }
        )
    _write(path, items)
    notifier = Notifier(Config(), path)
    dlg = AlertHistoryDialog(notifier)
    try:
        # max_history=200 是既有语义（只保留最近 200 条），断言按该上限
        assert dlg.list.count() == notifier.max_history
    finally:
        dlg.deleteLater()


# ================================================================== 目标 1-C
# 超平台 time_t 的时间戳
# ==================================================================
@pytest.mark.parametrize("bad_at", BAD_ATS)
def test_r5_alert_history_dialog_survives_out_of_range_at(tmp_path, bad_at):
    """at 超出平台 time_t 范围：窗口构建不得抛（本轮 BUG-3）。"""
    path = tmp_path / "alert_history.json"
    _write(path, [{"kind": "pct", "side": "up", "title": "t", "body": "b", "at": bad_at}])
    notifier = Notifier(Config(), path)
    dlg = AlertHistoryDialog(notifier)
    try:
        assert dlg.list.count() == 1
    finally:
        dlg.deleteLater()


@pytest.mark.parametrize("bad_at", [1e308, -1e308, 1e30, -1e30])
@pytest.mark.parametrize("renderer", ["price", "cache"])
def test_r5_bubble_renders_out_of_range_updated_at(bad_at, renderer):
    """气泡渲染遇到超范围 updated_at 不得抛（本轮 BUG-4）。"""
    from gold_pet import bubble

    q = Quote(
        code="x",
        name="黄金",
        price=900.0,
        prev_close=899.0,
        high=901.0,
        low=898.0,
        unit="元/克",
        currency="CNY",
    )
    if renderer == "price":
        html = bubble.render_price_html("黄金", q, updated_at=bad_at)
    else:
        html = bubble.render_cache_html("黄金", q, updated_at=bad_at)
    assert isinstance(html, str) and html


@pytest.mark.parametrize("bad_now", [1e308, -1e308, 1e30, -1e30, None])
def test_r5_spark_cutoff_and_evaluate_survive_out_of_range_now(tmp_path, bad_now):
    """spark_cutoff('today') 与 Notifier.evaluate 不得因子虚时钟炸穿。"""
    if bad_now is not None:
        value = spark_cutoff("today", bad_now)
        assert value is None or isinstance(value, float)
    notifier = Notifier(Config(), tmp_path / "alert_history.json")
    q = Quote(
        code="x",
        name="黄金",
        price=900.0,
        prev_close=899.0,
        high=901.0,
        low=898.0,
        unit="元/克",
        currency="CNY",
    )
    if bad_now is not None:
        alerts = notifier.evaluate(q, "黄金", now=bad_now)
        assert isinstance(alerts, list)


def test_r5_filter_series_out_of_range_points(tmp_path):
    """走势序列含超范围/字符串 t：filter_series 不得抛，值恒为有限 float。"""
    series = [
        {"t": "1700000000", "p": 900.0},
        {"t": 1e308, "p": 901.0},
        {"t": None, "p": 902.0},
        {"t": -1e308, "p": 903.0},
    ]
    for range_key in ("1h", "today", "all"):
        values = filter_series(series, range_key)
        assert all(isinstance(v, float) and v == v and abs(v) != float("inf") for v in values)


def test_r5_out_of_range_at_burst(tmp_path):
    """500 条随机超范围 at：窗口构建恒不抛。"""
    path = tmp_path / "alert_history.json"
    items = [
        {
            "kind": "pct",
            "side": "up",
            "title": "t",
            "body": "b",
            "at": _RND.choice(BAD_ATS + [1700000000.0, -1.0, 0.0, 1e12]),
        }
        for _ in range(500)
    ]
    _write(path, items)
    notifier = Notifier(Config(), path)
    dlg = AlertHistoryDialog(notifier)
    try:
        assert dlg.list.count() == notifier.max_history
    finally:
        dlg.deleteLater()


# ================================================================== 目标 1-D
# 断言调整说明：`_SAFE_TS_HIGH` 是本轮新增的「时间戳合理上界」口径，
# 未收敛到该上界内的历史数据（如公元 3000 年后）在 UI 展示层按 "?" 处理，
# 属既有 cli._fmt_time 的同款做法（展示层兜底，不改数据层语义）。
# ==================================================================
def test_r5_timestamp_upper_bound_is_platform_safe():
    """口径锚定：_SAFE_TS_HIGH 必须能在本平台 localtime 成功格式化。"""
    assert isinstance(_SAFE_TS_HIGH, float)
    clock = time.strftime("%Y", time.localtime(_SAFE_TS_HIGH))
    assert clock.isdigit()
