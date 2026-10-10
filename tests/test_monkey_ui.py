"""Monkey 测试补充目标：ui.py 迷你走势图脏序列回归（M-11）。

种子：20261007；迭代：200 次随机脏历史序列。
M-11：_update_spark 在「范围内无点」的回退分支里调用了未导入的 _safe_float，
历史序列含数值字符串价格时（_valid_point 旧口径允许）触发 NameError 崩穿渲染链路。

oracle：_update_spark 对任意脏序列不抛异常。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)  # 项目根入 sys.path（同 test_main.py 惯例）

import random
import time

from PySide6.QtWidgets import QApplication

from gold_pet.config import Config
from gold_pet.price import HistoryStore, PriceService, Quote
from gold_pet.ui import PetWindow

SEED = 20261007
ITERATIONS = 200


def _make_window(tmp_path, cfg=None):
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    cfg = cfg or Config()
    # R3 测试基建：同 tests/test_ui.py::_window——关闭控制服务，避免测试写真实
    # %APPDATA% 且避免用例结束时 QThread 仍运行导致解释器退出 qFatal（0xC0000409）。
    cfg.set("control_enabled", False)
    svc = PriceService(tmp_path / "last.json")
    hist = HistoryStore(tmp_path / "history.json")
    w = PetWindow(
        cfg, svc, hist, auto_refresh=False, notifier_history_path=tmp_path / "alert_history.json"
    )
    return w


def test_monkey_ui_update_spark_dirty_series(tmp_path):
    """200 次脏历史序列：范围内无点的回退分支不炸（M-11）。"""
    rnd = random.Random(SEED)
    quote = Quote(
        code="t", name="n", price=900.0, prev_close=895.0, high=901.0, low=894.0, unit="元/克"
    )
    p_variants = [123.4, "123.4", None, "abc", float("nan"), 0]
    for _ in range(ITERATIONS):
        w = _make_window(tmp_path, cfg=Config({"spark_range": "1h"}))
        # 全部旧于 1h 窗口的点 => filter_series 返回空 => 走回退分支
        points = []
        for _ in range(rnd.randint(1, 6)):
            p = rnd.choice(p_variants)
            if isinstance(p, float) and p != p:
                continue  # NaN 点会被 HistoryStore 清洗，等价于不存在
            points.append({"t": time.time() - rnd.uniform(7200, 100000), "p": p})
        (tmp_path / "history.json").write_text(
            f'{{"gold|cny_g": {points!r}}}'.replace("'", '"'), encoding="utf-8"
        )
        try:
            w._update_spark(quote, "#ff4d4f")  # NameError/M-11 修复前此处崩
        finally:
            w.hide()
            w.close()
