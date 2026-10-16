"""Monkey 测试目标 3+4：config.py 配置加载/sanitize + bubble.render_line_html。

种子：20261004；迭代：配置 500 次 + 气泡 500 次。
语料（80% 非法 / 20% 合法边界）：截断 JSON/BOM/GBK 等错误编码字节/键类型错/
数值越界/NaN-Infinity 字面量/深层嵌套/HTML 注入向量/控制字符/1MB 文本。

oracle：Config.load 不抛异常且 sanitize 后所有值收敛到合法域，save() 落盘
合法 JSON；render_line_html 永不抛异常、输出中不残留未转义的 <script>/<img>。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)  # 项目根入 sys.path（同 test_main.py 惯例）

import json
import random
import time

from _monkey_common import (
    assert_valid_json_text,
    dirty_value,
    json_with_nan,
    pick_number,
    pick_text,
    truncated_json,
    weird_bytes,
)

from gold_pet.bubble import render_line_html
from gold_pet.config import (
    ACTIVITY_LEVELS,
    CARE_INTERVAL_CHOICES,
    DEFAULTS,
    INTERVAL_CHOICES,
    SPARK_RANGE_NAMES,
    Config,
)

SEED = 20261004
ITERATIONS = 500


# ---------------------------------------------------------------- 目标 3：配置
def _dirty_settings(rnd: random.Random) -> bytes | str | dict:
    roll = rnd.random()
    if roll < 0.1:
        return truncated_json(rnd)
    if roll < 0.2:
        return weird_bytes(rnd)
    if roll < 0.3:
        return json_with_nan()
    if roll < 0.4:
        return "\ufeff" + json.dumps({"metal": "gold"})
    if roll < 0.5:
        return rnd.choice([b"", b"{", b"null", b"[1,2]", b'"str"', b"not json at all"])
    if roll < 0.7:
        # 键存在但类型/值脏：NaN、超大数、嵌套结构、垃圾时间
        return {key: pick_number(rnd) for key in rnd.sample(list(DEFAULTS), k=rnd.randint(1, 8))}
    if roll < 0.8:
        return dirty_value(rnd, max_depth=6)
    # 20%：合法边界值
    return {
        "metal": rnd.choice(["gold", "silver", "platinum"]),
        "unit": rnd.choice(["cny_g", "usd_oz"]),
        "interval_sec": rnd.choice(INTERVAL_CHOICES),
        "alert_pct": rnd.choice([0.0, 0.5, 20.0]),
        "alert_cooldown_sec": rnd.choice([60, 600, 7200]),
        "opacity": rnd.choice([0.3, 0.66, 1.0]),
        "dnd_start": rnd.choice(["00:00", "23:59", "08:30"]),
        "dnd_end": rnd.choice(["00:00", "23:59", "08:00"]),
        "pos": rnd.choice([None, [0, 0], [-1920, 1080]]),
        "spark_range": rnd.choice(list(SPARK_RANGE_NAMES)),
        "activity_level": rnd.choice(list(ACTIVITY_LEVELS)),
    }


def test_monkey_config_load_sanitize(tmp_path):
    """500 次脏配置加载：不抛异常、值域收敛、save 回写合法 JSON。"""
    rnd = random.Random(SEED)
    path = tmp_path / "settings.json"

    for _ in range(ITERATIONS):
        payload = _dirty_settings(rnd)
        if isinstance(payload, bytes):
            path.write_bytes(payload)
        elif isinstance(payload, str):
            # surrogatepass：孤代理对语料按非法 UTF-8 字节落盘（load 必须容错）
            path.write_bytes(payload.encode("utf-8", "surrogatepass"))
        else:
            try:
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            except UnicodeEncodeError:
                path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")

        cfg = Config.load(path)  # 抛任何异常即失败（M-03）

        d = cfg.as_dict()
        assert d["metal"] in ("gold", "silver", "platinum")
        assert d["unit"] in ("cny_g", "usd_oz")
        assert d["interval_sec"] in INTERVAL_CHOICES
        assert 0.0 <= d["alert_pct"] <= 20.0
        assert 60 <= d["alert_cooldown_sec"] <= 7200
        assert 0.0 <= d["alert_extreme_step_pct"] <= 5.0
        assert 0.3 <= d["opacity"] <= 1.0
        assert d["alert_pct"] == d["alert_pct"] and d["opacity"] == d["opacity"]  # 非 NaN
        assert d["spark_range"] in SPARK_RANGE_NAMES
        assert d["activity_level"] in ACTIVITY_LEVELS
        assert d["care_drink_interval_min"] in CARE_INTERVAL_CHOICES
        assert d["care_posture_interval_min"] in CARE_INTERVAL_CHOICES
        assert d["care_eye_interval_min"] in CARE_INTERVAL_CHOICES
        for key in ("dnd_start", "dnd_end"):
            text = d[key]
            assert isinstance(text, str) and len(text) == 5 and text[2] == ":"
        assert d["pos"] is None or (
            isinstance(d["pos"], list)
            and len(d["pos"]) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) for v in d["pos"])
        )
        for key in ("target_high", "target_low"):
            assert d[key] == d[key] and d[key] >= 0

        # save 回写必须是合法 JSON 且无 NaN/Infinity 非法字面量
        cfg.save()
        assert_valid_json_text(path.read_text(encoding="utf-8"))


def test_monkey_config_nan_literals_regression(tmp_path):
    """M-03 定向回归：JSON 里的 NaN/Infinity 不再炸穿 sanitize/load。"""
    path = tmp_path / "settings.json"
    cases = [
        '{"interval_sec": NaN}',
        '{"interval_sec": Infinity, "alert_cooldown_sec": -Infinity}',
        '{"opacity": NaN, "alert_pct": Infinity}',
        '{"care_drink_interval_min": Infinity, "care_eye_interval_min": NaN}',
        '{"target_high": NaN, "target_low": Infinity, "pos": NaN}',
    ]
    for text in cases:
        path.write_text(text, encoding="utf-8")
        cfg = Config.load(path)
        assert cfg.get("interval_sec") in INTERVAL_CHOICES
        assert 0.3 <= cfg.get("opacity") <= 1.0
        assert 0.0 <= cfg.get("alert_pct") <= 20.0
        assert cfg.get("care_drink_interval_min") in CARE_INTERVAL_CHOICES
        assert cfg.get("target_high") >= 0 and cfg.get("target_high") == cfg.get("target_high")


# ---------------------------------------------------------------- 目标 4：气泡
def test_monkey_bubble_render_line_html():
    """500 次随机文本（含 1MB 重型语料）：永远安全转义、不炸。"""
    rnd = random.Random(SEED + 1)
    idx = [0]
    attack_vectors = [
        "<script>alert(1)</script>",
        '<img src="x" onerror="steal()">',
        "<table><tr><td>嵌套标签",
        "</div></div><style>body{display:none}</style>",
        '<a href="javascript:alert(1)">点我</a>',
        "&lt;script&gt;已转义样本&lt;/script&gt;",
        "&#x3C;script&#x3E;",
        "\x00\x01\x02\x1b[31m控制字符\x07",
        "\u202e\u202d\ufeff零宽与双向控制",
        "🐳" * 100,
    ]
    for _ in range(ITERATIONS):
        idx[0] += 1
        i = idx[0]
        if i % 100 == 50:
            text = "A" * (1024 * 1024)  # 1MB
        elif rnd.random() < 0.8:
            text = rnd.choice(attack_vectors)
            if rnd.random() < 0.5:
                text = pick_text(rnd) + text + pick_text(rnd)
        else:
            text = pick_text(rnd)

        out = render_line_html(text)  # 抛任何异常即失败

        assert out.startswith('<div style="font-size:12px') and out.endswith("</div>")
        # 严格断言：原文里的标签开括号必须被转义，输出不残留可执行标签
        if "<script" in text or "<img" in text:
            assert "&lt;script" in out or "&lt;img" in out
        assert "<script" not in out and "<img " not in out


def test_monkey_bubble_render_1mb_perf():
    """1MB 文本单次渲染 ≤2s。"""
    start = time.perf_counter()
    out = render_line_html("<img>" + "x" * (1024 * 1024))
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0
    assert "&lt;img&gt;" in out
