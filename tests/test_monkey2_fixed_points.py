"""Monkey 测试 R2 目标 1：打第一轮修复代码本身（变体绕过）。

种子：20261011（与一轮 20261002~07 全部不同）；迭代：≥500。
攻击面（一轮修复点的绕过变体）：
- M-03/M-04/M-05/M-07 的「safe 转换器」只接 TypeError/ValueError——
  JSON 任意精度整数字面量（400 位以上）让 float() 抛 OverflowError（R2-M-01）
- M-01 的 _to_coord 只挡非有限值，不挡量级（R2-M-02 在 ui 修复）
- M-07 的「CLI --json 恒为合法 JSON」：prev_close 极小时 change_pct=±inf（R2-M-04）
- P2-01 的 /say 500 字截断：孤代理对/零宽字符在截断边界的组合
- 转换器喂 bytes / Decimal / bytearray 等 JSON 不可达但 Python 可达的变体

oracle：不抛未处理异常；输出/落盘恒为合法 JSON（parse_constant 钩子）；
/say 响应 ≤2s 且分发文本长度 ≤500。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 项目根入 sys.path

import json
import random
import threading
from decimal import Decimal

import httpx
import pytest
from _monkey_common import assert_valid_json_text

from gold_pet.config import Config, _as_float, _as_int
from gold_pet.control_server import _to_coord, build_server
from gold_pet.notifier import _safe_float
from gold_pet.price import HistoryStore, PriceService, Quote, Snapshot, _num, _valid_point

SEED = 20261011
ITERATIONS = 500
TOKEN = "monkey2-token"


def _rnd_huge_int(rnd: random.Random) -> int:
    """任意精度大整数字面量（JSON 直接可写，R2-M-01 的载体）。"""
    bits = rnd.choice([64, 128, 256, 400, 1024, 4000])
    v = rnd.getrandbits(bits)
    return -v if rnd.random() < 0.5 else v


# ---------------------------------------------------------------- 转换器直击
def test_r2_converters_overflow_and_variants(tmp_path):
    """500 次：JSON 可达 + Python 变体（bytes/Decimal/bytearray）全部安全收敛。"""
    rnd = random.Random(SEED)
    variants = [
        10**400,
        -(10**400),
        10**1024,
        2**5000,
        b"1.5",
        bytearray(b"2.5"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("1e999"),
        "1e999",
        "-1e999",
        1e308 * 10,
        -1e308 * 10,
        float("inf"),
        float("-inf"),
        float("nan"),
        True,
        False,
        None,
        [1],
        {"a": 1},
        "0x10",
        "",
        "   ",
    ]
    for _ in range(ITERATIONS):
        v = rnd.choice(variants) if rnd.random() < 0.7 else _rnd_huge_int(rnd)
        # 全部 safe 转换器必须返回值而非抛异常（含 OverflowError，R2-M-01）
        assert _safe_float(v, 0.0) is not None or v is None
        assert isinstance(_num(v, 0.0), float)
        assert isinstance(_as_float(v, 1.0), float)
        assert isinstance(_as_int(v, 60), int)
        r = _to_coord(v)
        assert r is None or isinstance(r, int)
        assert _valid_point({"t": v, "p": v}) in (True, False)


def test_r2_huge_int_through_loaders(tmp_path):
    """必溢出大整数（≥400 位）走完整加载链：配置拒启/历史崩/缓存崩都必须被拦住（R2-M-01）。"""
    rnd = random.Random(SEED + 1)
    hp = tmp_path / "history.json"
    cp = tmp_path / "cache.json"
    sp = tmp_path / "settings.json"
    for _ in range(ITERATIONS):
        huge = 10 ** rnd.choice([400, 1024, 4000]) * rnd.choice([1, -1])
        hp.write_text(
            json.dumps({"gold|cny_g": [{"t": 1, "p": 1}, {"t": huge, "p": 2}]}), encoding="utf-8"
        )
        hs = HistoryStore(hp)
        data = hs.load()
        assert isinstance(data, dict) and "gold|cny_g" in data
        assert len(data["gold|cny_g"]) == 1  # 大整数点被剔除

        cp.write_text(
            json.dumps({"quotes": {"gold|cny_g": {"price": huge, "prev_close": huge}}, "ok": True}),
            encoding="utf-8",
        )
        snap = PriceService(cp).load_cache()
        assert snap is not None and isinstance(snap.fx, float)

        sp.write_text(
            json.dumps({"interval_sec": huge, "alert_pct": huge, "opacity": huge}), encoding="utf-8"
        )
        cfg = Config.load(sp)
        assert cfg.get("interval_sec") in (15, 30, 60, 300, 900)
        assert 0.0 <= cfg.get("opacity") <= 1.0

        # filter_series / _safe_float 同链路
        from gold_pet.notifier import filter_series

        assert filter_series([{"t": 1, "p": huge}], "all") == []


# ---------------------------------------------------------------- Quote 有限性
def test_r2_quote_change_finite():
    """500 次：change/change_pct 对任意 (price, prev_close) 恒有限，--json 恒合法（R2-M-04）。"""
    rnd = random.Random(SEED + 2)
    magnitudes = [0.0, 1e-320, 1e-308, 1e-10, 1.0, -1.0, 1e10, 1e300, 1.7e308, -1.7e308, 10**400]
    for _ in range(ITERATIONS):
        price = rnd.choice(magnitudes) * rnd.choice([1, -1])
        prev = rnd.choice(magnitudes) * rnd.choice([1, -1])
        if rnd.random() < 0.3:
            price = abs(rnd.uniform(0, 1000))
            prev = abs(rnd.uniform(0, 1000))
        q = Quote(code="t", name="t", price=price, prev_close=prev, high=0.0, low=0.0)
        text = json.dumps(
            {
                "change": round(q.change, 4),
                "change_pct": round(q.change_pct, 3),
                "p": q.format_price(),
            },
            allow_nan=False,
        )
        assert_valid_json_text(text)  # 含 NaN/Infinity 即失败
        # -0.0 显示归一（P3）
        if q.price == 0:
            assert not q.format_price().startswith("-")


def test_r2_cli_price_json_legal(tmp_path, monkeypatch, capsys):
    """脏缓存（极小 prev_close）走 cli.main(['price','--json'])：stdout 恒为合法 JSON。"""
    import cli as cli_mod

    raw = {
        "quotes": {
            "gold|cny_g": {
                "code": "gds_AUTD",
                "name": "x",
                "price": 1.0,
                "prev_close": 1e-308,
                "high": 1.0,
                "low": 1.0,
            }
        },
        "fx": 0.0,
        "updated_at": 1.0,
        "ok": True,
        "error": "",
    }
    cp = tmp_path / "last_price.json"
    cp.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(cli_mod, "cache_path", lambda: cp)
    monkeypatch.setattr(cli_mod, "_load_config", lambda: Config({}))
    rc = cli_mod.main(["price", "--json"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert_valid_json_text(out)  # R2-M-04 修复前这里输出 Infinity 字面量
    data = json.loads(out)
    assert data["ok"] is True


# ---------------------------------------------------------------- /say 截断边界
def _state_reader() -> dict:
    return {"app": "gold-pet", "version": "0.8.0", "state": "idle", "fatty_count": 0}


@pytest.fixture(scope="module")
def pet_server():
    actions: list[dict] = []
    server = build_server(("127.0.0.1", 0), TOKEN, _state_reader)
    server.on_action = actions.append
    th = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", actions
    server.shutdown()
    server.server_close()


def test_r2_say_truncation_boundaries(pet_server):
    """300 次：500 字截断边界上的孤代理对/零宽/组合字符——响应恒为合法 JSON、分发恒 ≤500 字。"""
    base, actions = pet_server
    rnd = random.Random(SEED + 3)
    units = ["\ud800", "\udfff", "\u200b", "\u0301", "🐳", "\t", " ", "a"]
    with httpx.Client(base_url=base, headers={"X-GP-Token": TOKEN}, timeout=2.0) as s:
        before = len(actions)
        for _ in range(300):
            text = "".join(rnd.choice(units) for _ in range(rnd.randint(495, 510)))
            # 手工编码：httpx 的 json= 无法发送孤代理对（ensure_ascii=False + utf-8 编码会炸），
            # 而 \udXXX 转义正是服务端必须消化的合法 JSON 输入
            content = json.dumps({"text": text}, ensure_ascii=True).encode("utf-8")
            r = s.post(
                "/say", content=content, headers={"Content-Type": "application/json; charset=utf-8"}
            )
            assert r.status_code == 200, r.text[:120]
            assert isinstance(r.json().get("ok"), bool)
        got = actions[before:]
        assert len(got) == 300
        for a in got:
            assert len(a["text"]) <= 500
            json.dumps(a, ensure_ascii=False)  # 分发的动作必须可序列化


def test_r2_to_coord_magnitude_variants():
    """_to_coord 量级变体：int32 内返回 int、超界由 UI 层夹取（R2-M-02 配套），绝不抛。"""
    for v in [0, 1, -(2**31), 2**31 - 1, 2**31, 2**40, 10**400, -(10**400), -0.0, 3.7, -3.7]:
        r = _to_coord(v)
        assert r is None or isinstance(r, int)


def test_r2_decimal_bytes_via_snapshot(tmp_path):
    """Decimal/bytes 等非 JSON 类型经 Snapshot.from_dict 的收敛路径（_num 内部）。"""
    snap = Snapshot.from_dict(
        {"quotes": {"k": {"price": Decimal("NaN"), "prev_close": b"1"}}, "fx": Decimal("1e999")}
    )
    assert isinstance(snap.fx, float)
    assert "k" in snap.quotes  # price 0.0 兜底，prev_close 0.0，均为有限值
    assert all(
        v == v and abs(v) != float("inf")
        for v in (snap.quotes["k"].price, snap.quotes["k"].prev_close)
    )


def test_r2_notifier_history_cap_and_legal_file(tmp_path):
    """record 大量超长 at 值：落盘恒合法、条数恒 ≤ 上限（一轮 M-05 的变体回归）。"""
    from gold_pet.notifier import Alert, Notifier

    n = Notifier(Config({}), history_path=tmp_path / "ah.json", max_history=50)
    weird_ats = [10**400, -(10**400), 1e308, -1e308, "123", None, b"1"]
    for i in range(500):
        at = weird_ats[i % len(weird_ats)]
        try:
            n.record([Alert(kind="pct", side="up", title="t", body="b", at=at)])
        except (OverflowError, TypeError, ValueError):
            # Alert.at 由调用方构造，非常规脏值允许在构造/排序期被拒，
            # 但绝不能污染已落盘文件
            pass
        text = (tmp_path / "ah.json").read_text(encoding="utf-8")
        assert_valid_json_text(text)
        assert len(n._history) <= 50
