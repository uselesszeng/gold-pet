"""命令行查行情（调试用）：python tools/check_price.py"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gold_pet.price import METALS, fetch_raw, parse_snapshot  # noqa: E402


def main() -> int:
    snap = parse_snapshot(fetch_raw())
    if not snap.ok:
        print("获取失败：", snap.error)
        return 1
    for metal, meta in METALS.items():
        for unit in ("cny_g", "usd_oz"):
            q = snap.price_of(metal, unit)
            if q is None:
                continue
            arrow = "▲" if q.change > 0 else "▼" if q.change < 0 else "—"
            tag = " (换算)" if q.converted else ""
            print(
                f"{meta['name']:<4} {q.format_price():>12} {q.unit:<10} "
                f"{arrow} {q.change:+.2f} ({q.change_pct:+.2f}%){tag}"
            )
    print(f"汇率 USD/CNY：{snap.fx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
