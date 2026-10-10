"""Monkey 测试共享语料生成器（非测试文件，pytest 不收集）。

约定：固定随机种子（见各 test_monkey_*.py 模块头），语料按
「80% 非法 / 20% 合法边界值」混合，标准库 random/json/string 实现，零新增依赖。
"""

from __future__ import annotations

import json
import random

# ---------------------------------------------------------------- 语料池
# 合法边界值（约 20% 概率被抽中）
VALID_TEXTS = [
    "主人好呀",
    "a",
    "x" * 499,  # /say 限长 500 的边界
    "x" * 500,
    "x" * 501,  # 越界 1 字符
    "黄金 999.9 ▲",
    "emoji 🐳🐟😂 混排 ＡＢＣ",
]
VALID_NUMBERS = [0, 1, -1, 100, -100, 2**31, -(2**31), 0.5, -0.5, 1e10, -1e10, 10**15]

# 非法值（约 80% 概率被抽中）
DIRTY_TEXTS = [
    "",
    "   ",
    "\n\t\r\x00\x01\x07\x1b[31m",
    "<script>alert(1)</script>",
    '<img src="x" onerror="alert(1)">',
    "&amp;&lt;&gt;&quot;&#x27;",
    "javascript:alert(1)",
    "\u202e逆转\u202d文本",
    "🐳" * 300,
    "null",
    "NaN",
    "\ud800\udfff 单独代理对",  # 注：Python str 可含孤代理，JSON 编码侧由 ensure_ascii 处理
    "中文" + "\x00" * 50 + "混控制字符",
    "'" + '"' * 20 + "\\" * 20,
]

JUNK_KEYS = ["", "\x00key", "键🔑", "a" * 300, "NaN", "0", "-1", "\u202e"]

# JSON 文本里可直接写入的非法字面量（json.loads 默认容忍，但写盘即非法 JSON）
NAN_LITERALS = ["NaN", "Infinity", "-Infinity"]

HUGE_NUMBERS = [10**308, 10**50, -(10**50), 2**63, -(2**63), 1.7e308, -1.7e308]


def pick_text(rnd: random.Random) -> str:
    """80% 非法文本 / 20% 合法边界文本。"""
    if rnd.random() < 0.8:
        return rnd.choice(DIRTY_TEXTS)
    return rnd.choice(VALID_TEXTS)


def pick_number(rnd: random.Random):
    """80% 非法数值（None/bool/NaN/Inf/超大/字符串数字/垃圾字符串）/ 20% 合法边界。"""
    roll = rnd.random()
    if roll < 0.2:
        return rnd.choice(VALID_NUMBERS)
    if roll < 0.35:
        return rnd.choice([None, True, False])
    if roll < 0.5:
        return rnd.choice(HUGE_NUMBERS)
    if roll < 0.6:
        return rnd.choice(["123", "-4.5", "1e400", "nan", "0x10", "", "　１２３"])
    if roll < 0.7:
        return rnd.choice([float("nan"), float("inf"), float("-inf")])
    if roll < 0.8:
        return rnd.choice([[1, 2], {"a": 1}, [None], {"self": []}])
    return rnd.choice(["😤", "\x00", "a" * 200])


def dirty_value(rnd: random.Random, depth: int = 0, max_depth: int = 3):
    """递归脏 JSON 值：null/bool/数字/字符串/数组/对象混合，80% 非法倾向。"""
    roll = rnd.random()
    if depth >= max_depth or roll < 0.4:
        if rnd.random() < 0.5:
            return pick_number(rnd)
        return pick_text(rnd)
    if roll < 0.7:
        return [dirty_value(rnd, depth + 1, max_depth) for _ in range(rnd.randint(0, 4))]
    return {
        (rnd.choice(JUNK_KEYS) if rnd.random() < 0.7 else f"k{i}"): dirty_value(
            rnd, depth + 1, max_depth
        )
        for i in range(rnd.randint(0, 4))
    }


def deep_nested_json(depth: int) -> bytes:
    """n 层嵌套数组 JSON（json 解析器在数千层触发 RecursionError）。"""
    return ("[" * depth + "]" * depth).encode("ascii")


def json_with_nan() -> str:
    """含 NaN/Infinity 字面量的 JSON 文本。"""
    return '{"a": NaN, "b": [Infinity, -Infinity, 1]}'


def json_with_dup_keys() -> str:
    """重复键 JSON：解析器取最后出现的值，不应崩溃。"""
    return '{"dx": 1, "dx": "str", "dx": null, "text": "a", "text": 2, "text": [1]}'


def weird_bytes(rnd: random.Random) -> bytes:
    """非法 UTF-8 / BOM / 编码错乱的原始字节。"""
    choice = rnd.randint(0, 3)
    if choice == 0:
        return b"\xff\xfe{\x00bad-utf8"
    if choice == 1:
        return b"\xef\xbb\xbf" + json.dumps({"text": "带 BOM 的合法 JSON"}).encode("utf-8")
    if choice == 2:
        # GBK 编码的 JSON 字节（按 UTF-8 读会 UnicodeDecodeError）
        return '{"text": "GBK 中文"}'.encode("gbk")
    return b'{"text": "\\ud800\\udc00"}'  # 孤代理对转义


def truncated_json(rnd: random.Random) -> bytes:
    """截断 JSON：完整样本随机切一刀。"""
    sample = json.dumps({"text": "完整样本" * 10, "dx": 1, "dy": 2}, ensure_ascii=False)
    cut = rnd.randint(1, max(1, len(sample) - 1))
    return sample[:cut].encode("utf-8")


def assert_valid_json_text(text: str) -> None:
    """状态文件 oracle：文本必须是合法 JSON 且不含 NaN/Infinity 非法字面量。

    用 parse_constant 钩子精确检测字面量（子串匹配会把键名里的 "NaN" 误判）。
    """

    def _reject(name: str) -> None:
        raise AssertionError(f"状态文件含非法 JSON 字面量 {name}: {text[:200]!r}")

    json.loads(text, parse_constant=_reject)
