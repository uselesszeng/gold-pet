"""行情抓取与解析（新浪财经公开接口，免密钥）。

品种与单位：
  黄金  cny_g  gds_AUTD（上海金交所 Au T+D，元/克）  usd_oz hf_XAU（伦敦金，美元/盎司）
  白银  cny_g  gds_AGTD（白银 T+D，元/千克 ÷ 1000）  usd_oz hf_XAG（伦敦银）
  铂金  usd_oz hf_XPT（纽约铂金）                    cny_g 由美元价换算

字段顺序（新浪 hf_/gds_ 通用）：
  0 现价 | 4 最高 | 5 最低 | 6 时间 | 7 昨收 | 12 日期 | 13 名称
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

SINA_URL = "https://hq.sinajs.cn/list=gds_AUTD,hf_XAU,gds_AGTD,hf_XAG,hf_XPT,fx_susdcny"
HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}
OZ_TO_GRAM = 31.1034768

CODE_FX = "fx_susdcny"

# 品种 -> 单位 -> (接口代码, 缩放系数)；cny_g 缺失时自动用美元价换算
METALS: dict[str, dict] = {
    "gold": {
        "name": "黄金",
        "sources": {"cny_g": ("gds_AUTD", 1.0), "usd_oz": ("hf_XAU", 1.0)},
    },
    "silver": {
        "name": "白银",
        "sources": {"cny_g": ("gds_AGTD", 0.001), "usd_oz": ("hf_XAG", 1.0)},
    },
    "platinum": {
        "name": "铂金",
        "sources": {"usd_oz": ("hf_XPT", 1.0)},
    },
}

_PATTERN = re.compile(r'var hq_str_(\w+)="([^"]*)";')


def key(metal: str, unit: str) -> str:
    return f"{metal}|{unit}"


@dataclass
class Quote:
    """单个品种的行情快照。"""

    code: str
    name: str
    price: float
    prev_close: float
    high: float = 0.0
    low: float = 0.0
    time_text: str = ""
    unit: str = ""
    currency: str = ""
    converted: bool = False

    @property
    def change(self) -> float:
        return self.price - self.prev_close if self.prev_close else 0.0

    @property
    def change_pct(self) -> float:
        return self.change / self.prev_close * 100 if self.prev_close else 0.0

    def format_price(self) -> str:
        digits = 2 if self.price >= 10 else 3
        return f"{self.price:.{digits}f}"

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__annotations__}

    @classmethod
    def from_dict(cls, data: dict) -> Quote:
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})


@dataclass
class Snapshot:
    """一次抓取结果。"""

    quotes: dict[str, Quote] = field(default_factory=dict)
    fx: float = 0.0
    updated_at: float = field(default_factory=time.time)
    ok: bool = False
    error: str = ""

    def price_of(self, metal: str = "gold", unit: str = "cny_g") -> Quote | None:
        q = self.quotes.get(key(metal, unit))
        if q is not None:
            return q
        # 缺哪个方向就换算：元/克 <-> 美元/盎司
        if unit == "cny_g" and self.fx:
            base = self.quotes.get(key(metal, "usd_oz"))
            if base is not None:
                return Quote(
                    code=base.code,
                    name=f"{base.name}(换算)",
                    price=base.price / OZ_TO_GRAM * self.fx,
                    prev_close=base.prev_close / OZ_TO_GRAM * self.fx,
                    high=base.high / OZ_TO_GRAM * self.fx,
                    low=base.low / OZ_TO_GRAM * self.fx,
                    unit="元/克",
                    currency="CNY",
                    converted=True,
                )
        if unit == "usd_oz" and self.fx:
            base = self.quotes.get(key(metal, "cny_g"))
            if base is not None:
                return Quote(
                    code=base.code,
                    name=f"{base.name}(换算)",
                    price=base.price * OZ_TO_GRAM / self.fx,
                    prev_close=base.prev_close * OZ_TO_GRAM / self.fx,
                    high=base.high * OZ_TO_GRAM / self.fx,
                    low=base.low * OZ_TO_GRAM / self.fx,
                    unit="美元/盎司",
                    currency="USD",
                    converted=True,
                )
        return None

    def to_dict(self) -> dict:
        return {
            "quotes": {k: v.to_dict() for k, v in self.quotes.items()},
            "fx": self.fx,
            "updated_at": self.updated_at,
            "ok": self.ok,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Snapshot:
        return cls(
            quotes={k: Quote.from_dict(v) for k, v in (data.get("quotes") or {}).items()},
            fx=data.get("fx", 0.0),
            updated_at=data.get("updated_at", time.time()),
            ok=data.get("ok", False),
            error=data.get("error", ""),
        )


def _f(values: list, idx: int, default: float = 0.0) -> float:
    try:
        return float(values[idx])
    except (IndexError, ValueError):
        return default


def _s(values: list, idx: int, default: str = "") -> str:
    try:
        return values[idx]
    except IndexError:
        return default


def fetch_raw(timeout: float = 8.0) -> dict[str, list]:
    req = urllib.request.Request(SINA_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("gbk", errors="ignore")
    return {code: payload.split(",") for code, payload in _PATTERN.findall(text)}


def parse_snapshot(raw: dict[str, list]) -> Snapshot:
    quotes: dict[str, Quote] = {}
    for metal, cfg in METALS.items():
        for unit, (code, scale) in cfg["sources"].items():
            fields = raw.get(code) or []
            if len(fields) < 9 or _f(fields, 0) <= 0:
                continue
            quotes[key(metal, unit)] = Quote(
                code=code,
                name=_s(fields, 13, cfg["name"]),
                price=_f(fields, 0) * scale,
                prev_close=_f(fields, 7) * scale,
                high=_f(fields, 4) * scale,
                low=_f(fields, 5) * scale,
                time_text=f"{_s(fields, 12)} {_s(fields, 6)}".strip(),
                unit="元/克" if unit == "cny_g" else "美元/盎司",
                currency="CNY" if unit == "cny_g" else "USD",
            )

    fx_raw = raw.get(CODE_FX) or []
    fx = _f(fx_raw, 1)
    if not (3.0 < fx < 12.0):
        fx = _f(fx_raw, 7)
    if not (3.0 < fx < 12.0):
        fx = 0.0

    return Snapshot(
        quotes=quotes,
        fx=fx,
        ok=bool(quotes),
        error="" if quotes else "行情数据为空",
    )


# 日盘时段（分钟数）：09:00-11:30、13:30-15:30
_DAY_SESSIONS = ((9 * 60, 11 * 60 + 30), (13 * 60 + 30, 15 * 60 + 30))
# 夜盘：当日 20:00 起，至次日 02:30
_NIGHT_START = 20 * 60
_NIGHT_END = 2 * 60 + 30


def is_market_open(now: time.struct_time | None = None) -> bool:
    """Au(T+D) / 上海金交所是否在交易时段（按本机本地时间判断，默认东八区）。

    日盘：09:00-11:30、13:30-15:30；夜盘：当日 20:00 - 次日 02:30。

    夜盘归属规则（关键）：凌晨 00:00-02:30 属于**前一个交易日**的夜盘。
    因此周一凌晨的夜盘实际是上周五夜盘，周末休市，需要单独排除；
    周六凌晨 00:00-02:30 属于周五夜盘，仍在交易。

    注意：若用户时区不是东八区判断会有偏差，这里只用于气泡上的「休市」提示。
    """
    now = now or time.localtime()
    weekday = now.tm_wday  # 0=周一 … 5=周六 6=周日
    minutes = now.tm_hour * 60 + now.tm_min

    # 凌晨 00:00-02:30：属于「前一日」夜盘的延续
    if minutes <= _NIGHT_END:
        prev_weekday = (weekday - 1) % 7
        # 前一日为周一至周五才有夜盘；周六凌晨是周五夜盘，周一凌晨是周日（无夜盘）
        return prev_weekday < 5

    # 周末白天休市
    if weekday >= 5:
        return False

    # 日盘
    for start, end in _DAY_SESSIONS:
        if start <= minutes <= end:
            return True

    # 夜盘前段：当日 20:00 以后
    return minutes >= _NIGHT_START


class PriceService:
    """行情服务：抓取 + 本地缓存（断网时回显上次价格）。"""

    def __init__(self, cache_path: Path):
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    def fetch(self, timeout: float = 8.0) -> Snapshot:
        try:
            snap = parse_snapshot(fetch_raw(timeout))
        except Exception as exc:
            snap = Snapshot(ok=False, error=f"{type(exc).__name__}: {exc}")
        if snap.ok:
            self.save_cache(snap)
        return snap

    def save_cache(self, snap: Snapshot) -> None:
        try:
            self.cache_path.write_text(
                json.dumps(snap.to_dict(), ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def load_cache(self) -> Snapshot | None:
        try:
            return Snapshot.from_dict(json.loads(self.cache_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            return None


class HistoryStore:
    """价格历史（用于迷你走势图），每个品种+单位最多保留 max_points 条。"""

    def __init__(self, path: Path, max_points: int = 120):
        self.path = Path(path)
        self.max_points = max_points
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, list[dict]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def append(self, metal: str, unit: str, price: float) -> list[dict]:
        data = self.load()
        series = data.get(key(metal, unit), [])
        now = time.time()
        # 同一分钟内不重复记录
        if series and now - series[-1].get("t", 0) < 60:
            series[-1] = {"t": now, "p": price}
        else:
            series.append({"t": now, "p": price})
        series = series[-self.max_points :]
        data[key(metal, unit)] = series
        try:
            self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return series

    def series(self, metal: str, unit: str) -> list[dict]:
        return self.load().get(key(metal, unit), [])
