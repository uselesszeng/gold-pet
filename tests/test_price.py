"""行情解析 / 缓存 / 历史的单元测试。

运行：pytest tests -q
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gold_pet.price import (  # noqa: E402
    METALS,
    HistoryStore,
    PriceService,
    Snapshot,
    key,
    parse_snapshot,
)

# 真实接口字段顺序（0 现价 / 4 最高 / 5 最低 / 6 时间 / 7 昨收 / 12 日期 / 13 名称）
RAW = {
    "gds_AUTD": "897.15,0,896.03,897.25,902.00,889.24,15:29:54,900.37,897.00,45270,2,7,2026-09-29,黄金延期".split(","),
    "hf_XAU": "4138.36,4114.93,4138.36,4138.71,4148.85,4113.30,16:37:00,4114.93,4117.01,0,0,0,2026-09-29,伦敦金".split(","),
    "gds_AGTD": "14801.00,0,14795,14801,15010,14692,15:30:00,14850,14890,341344,1,19,2026-09-29,白银延期".split(","),
    "hf_XAG": "60.60,60.62,60.60,60.64,61.00,60.28,16:37:00,60.62,60.62,0,0,0,2026-09-29,伦敦银".split(","),
    "hf_XPT": "1709.91,,1710.90,1711.60,1741.20,1697.40,17:15:57,1744.70,1736.60,0,2,1,2026-09-29,纽约铂金".split(","),
    "fx_susdcny": "16:37:00,6.7038,6.7042,6.7118,61,6.7094,6.7094,6.7033,6.7042,x,-0.11,0,0,y,0,0,,2026-09-29".split(","),
}


def test_parse_all_metals():
    snap = parse_snapshot(RAW)
    assert snap.ok
    assert snap.quotes[key("gold", "cny_g")].price == 897.15
    assert snap.quotes[key("gold", "usd_oz")].price == 4138.36
    # 白银接口是元/千克，缩放后应为元/克
    assert abs(snap.quotes[key("silver", "cny_g")].price - 14.801) < 1e-6
    assert snap.quotes[key("silver", "usd_oz")].price == 60.60
    assert snap.quotes[key("platinum", "usd_oz")].price == 1709.91
    assert 6.7 < snap.fx < 6.71


def test_change_and_pct():
    snap = parse_snapshot(RAW)
    q = snap.price_of("gold", "cny_g")
    assert abs(q.change - (897.15 - 900.37)) < 1e-6
    assert q.change < 0
    assert abs(q.change_pct - (q.change / 900.37 * 100)) < 1e-6


def test_high_low():
    q = parse_snapshot(RAW).price_of("gold", "cny_g")
    assert q.high == 902.00
    assert q.low == 889.24


def test_platinum_cny_is_converted():
    snap = parse_snapshot(RAW)
    q = snap.price_of("platinum", "cny_g")
    assert q is not None and q.converted
    expect = 1709.91 / 31.1034768 * snap.fx
    assert abs(q.price - expect) < 1e-6


def test_price_of_missing_returns_none():
    snap = parse_snapshot({})
    assert snap.ok is False
    assert snap.price_of("gold", "cny_g") is None


def test_snapshot_roundtrip():
    snap = parse_snapshot(RAW)
    back = Snapshot.from_dict(snap.to_dict())
    assert back.ok
    assert abs(back.price_of("gold", "cny_g").price - 897.15) < 1e-6
    assert abs(back.fx - snap.fx) < 1e-9


def test_cache_save_load(tmp_path):
    svc = PriceService(tmp_path / "last.json")
    snap = parse_snapshot(RAW)
    svc.save_cache(snap)
    loaded = svc.load_cache()
    assert loaded is not None and loaded.ok
    assert abs(loaded.price_of("gold", "cny_g").price - 897.15) < 1e-6


def test_cache_corrupted_returns_none(tmp_path):
    svc = PriceService(tmp_path / "last.json")
    (tmp_path / "last.json").write_text("{not json", encoding="utf-8")
    assert svc.load_cache() is None


def test_history_dedupe_and_cap(tmp_path):
    store = HistoryStore(tmp_path / "h.json", max_points=5)
    store.append("gold", "cny_g", 900.0)
    store.append("gold", "cny_g", 901.0)  # 60s 内覆盖，不新增
    assert len(store.series("gold", "cny_g")) == 1
    assert store.series("gold", "cny_g")[-1]["p"] == 901.0

    data = store.load()
    data[key("gold", "cny_g")] = [{"t": time.time() - i * 120, "p": 100 + i} for i in range(10)]
    (tmp_path / "h.json").write_text(
        __import__("json").dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    store.append("gold", "cny_g", 999.0)
    assert len(store.series("gold", "cny_g")) == 5  # 上限生效


def test_metals_config_shape():
    for metal, meta in METALS.items():
        assert "name" in meta and meta["sources"]
        for unit, spec in meta["sources"].items():
            assert unit in ("cny_g", "usd_oz")
            assert len(spec) == 2  # (code, scale)
