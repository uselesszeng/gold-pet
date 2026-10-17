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
import logging
import math
import os
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("gold_pet")

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
        """R2-M-04：prev_close 极小/异号巨值时差值可溢出为 ±inf（或 huge int 直接
        溢出 float），收敛为 0.0——防止 CLI --json / 气泡输出非法 JSON 字面量。"""
        try:
            value = float(self.price - self.prev_close if self.prev_close else 0.0)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return value if math.isfinite(value) else 0.0

    @property
    def change_pct(self) -> float:
        """R2-M-04：除以极小 prev_close 会得 ±inf，同样收敛为 0.0。"""
        try:
            denom = float(self.prev_close)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if not denom:
            return 0.0
        value = self.change / denom * 100
        return value if math.isfinite(value) else 0.0

    def format_price(self) -> str:
        try:
            value = float(self.price)
        except (TypeError, ValueError, OverflowError):
            value = 0.0
        # + 0.0 把 -0.0 归一为 0.0（手改缓存可能出现），避免显示 "-0.000"
        digits = 2 if value >= 10 else 3
        return f"{value + 0.0:.{digits}f}"

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__annotations__}

    @classmethod
    def from_dict(cls, data: dict) -> Quote:
        """M-07：缓存文件可能被手改坏——字段缺失/类型错/NaN 一律收敛，
        不让脏字段（如 price 为字符串）在后续 change/format_price 链路炸出 TypeError。
        数值字段出现非有限值时抛 ValueError，由 Snapshot.from_dict 剔除该条报价。
        """
        known = {k: v for k, v in data.items() if k in cls.__annotations__}
        price = _num(known.get("price"), 0.0)
        prev_close = _num(known.get("prev_close"), 0.0)
        high = _num(known.get("high"), 0.0)
        low = _num(known.get("low"), 0.0)
        if not all(math.isfinite(v) for v in (price, prev_close, high, low)):
            raise ValueError("quote 含非有限数值（NaN/Infinity）")
        return cls(
            code=str(known.get("code", "")),
            name=str(known.get("name", "")),
            price=price,
            prev_close=prev_close,
            high=high,
            low=low,
            time_text=str(known.get("time_text", "")),
            unit=str(known.get("unit", "")),
            currency=str(known.get("currency", "")),
            converted=bool(known.get("converted", False)),
        )


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
        """M-07：对脏缓存文件结构收敛——quotes 必须是 dict、元素必须是 dict，
        标量字段宽松转型；坏报价剔除而不是让 AttributeError/TypeError 炸穿
        load_cache（它只捕 OSError/ValueError/KeyError/TypeError）与 CLI。
        """
        if not isinstance(data, dict):
            data = {}
        quotes_raw = data.get("quotes")
        quotes: dict[str, Quote] = {}
        if isinstance(quotes_raw, dict):
            for k, v in quotes_raw.items():
                if isinstance(v, dict):
                    try:
                        quotes[str(k)] = Quote.from_dict(v)
                    except (TypeError, ValueError):
                        continue  # 单条坏报价不拖垮整个快照
        return cls(
            quotes=quotes,
            fx=_num(data.get("fx"), 0.0),
            updated_at=_num(data.get("updated_at"), 0.0, low=0.0, high=32503680000.0),
            ok=bool(data.get("ok", False)),
            error=str(data.get("error", "")),
        )


def _f(values: list, idx: int, default: float = 0.0) -> float:
    """R3-M-01：行情源文本字段转 float。

    上游返回 `nan` / `inf` / `1e999` 这类文本时 float() 能成功但产出非有限值，
    会被 parse_snapshot 当作有效报价，最终 json.dumps 把 NaN/Infinity 字面量
    写进 last_price.json（RFC 8259 之外的非法 JSON，严格解析器全部不认）。
    非有限值一律按坏字段回落 default（与 _num / _valid_point / _safe_float 同口径）。
    """
    try:
        value = float(values[idx])
    except (IndexError, ValueError, OverflowError):
        return default
    return value if math.isfinite(value) else default


def _num(
    value: object, default: float = 0.0, low: float | None = None, high: float | None = None
) -> float:
    """M-07：任意脏值 -> 有限 float；转换失败或超出 [low, high] 回落 default。

    R2-M-01：float(10**400)（JSON 超长整数字面量）抛 OverflowError 而非 ValueError，
    必须一并接住，否则脏缓存会让 Snapshot.from_dict 炸穿 load_cache。

    R5-R-01 / R5-04：入参声明为 object 是**有意的**（脏值来自被手改的 JSON），
    而 float() 的类型存根只接受 str / Buffer / SupportsFloat / SupportsIndex，
    CI 的 `mypy` 阶段因此报 1 个 error（`pyproject.toml` 的 `warn_unused_ignores = false`
    正是为这类必要 ignore 留的位置）。这里在调用点局部收窄为 Any 并显式标注
    `# type: ignore[arg-type]`，表达「就是要吃任意东西，转换失败由 except 兜住」，
    既不动「入参是任意脏值」这一契约，也不改任何运行期行为。
    """
    raw: Any = value
    try:
        result = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(result):
        return default
    if low is not None and result < low:
        return default
    if high is not None and result > high:
        return default
    return result


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
            # R2-M-05：原子写，PriceWorker 写盘期间 CLI/主线程读缓存不会看到半截 JSON
            _atomic_write_text(self.cache_path, json.dumps(snap.to_dict(), ensure_ascii=False))
        except OSError:
            pass

    def load_cache(self) -> Snapshot | None:
        try:
            return Snapshot.from_dict(json.loads(self.cache_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            # RecursionError：R2-M-03，同 HistoryStore.load
            return None


_write_seq = 0
_write_seq_lock = threading.Lock()


def _atomic_write_text(path: Path, text: str) -> None:
    """原子写文本：先写同目录唯一临时文件再 os.replace（R2-M-05）。

    直接 write_text 的话，并发写/读写交错时读者可能看到截断的半截 JSON；
    os.replace 在同一分区内是原子操作，读者要么看到旧文件要么看到完整新文件。
    临时文件名带 pid+线程标识：多个线程/进程同时写同一个目标时不会互相
    覆盖对方尚未 replace 的临时文件（否则仍可能交错出坏内容）。
    注意：Windows 上 replace 瞬间读者可能拿到 PermissionError（delete-pending），
    由调用方既有的 OSError 容错路径消化。
    """
    global _write_seq
    with _write_seq_lock:
        _write_seq += 1
        seq = _write_seq
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{seq}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# R5-BUG-4：时间戳格式化边界。
# time.localtime() 对超出平台 time_t 范围的输入抛 OverflowError（而非返回错误值），
# 手改/损坏的状态文件里出现 `"at": 1e308` 就会让「提醒历史」窗口、气泡渲染、
# spark_cutoff 全线崩。展示层统一走这个工具：任何异常一律回落占位符，
# 与 cli._fmt_time（早已这么兜）同口径——展示层兜底，不改数据层语义。
_FALLBACK_CLOCK = "?"
_SAFE_TS_LOW = 0.0
# 公元 3000 年（与 _num(updated_at, high=...) 既有口径一致），远超其上的都是坏数据
_SAFE_TS_HIGH = 32503680000.0


def safe_strftime(fmt: str, ts: object, fallback: str = _FALLBACK_CLOCK) -> str:
    """安全的时间戳格式化：脏值/超范围一律返回 fallback，绝不抛异常。

    入参 ts 可能来自手改的状态文件（字符串数字、None、1e308、-1e308、嵌套容器），
    因此这里同时承担「转 float」与「量级夹取」两件事：
      - 转换失败、非有限值、超出 [0, 3000 年] 的值 → fallback；
      - 转换成功且合理 → 按 fmt 格式化。
    注意不做静默归零（那会把 1e308 显示成 1970 年，比 "?" 更误导用户）。
    """
    try:
        value = float(ts)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return fallback
    if not math.isfinite(value) or not (_SAFE_TS_LOW <= value <= _SAFE_TS_HIGH):
        return fallback
    try:
        return time.strftime(fmt, time.localtime(value))
    except (OSError, OverflowError, ValueError):
        # 极端平台 time_t 位宽（32 位）下即使夹取后仍可能越界，兜底不抛
        return fallback


def _valid_point(point: object) -> bool:
    """P2-03：序列点合法性——必须是 dict 且 t/p 均可转 float 且为有限值。

    手改坏/磁盘损坏产生的坏点（非 dict、t/p 为字符串或 None）一律视为无效，
    避免渲染与写入链路 float()/dict.get 抛异常。
    M-04：NaN / Infinity 同样能通过 float()，但回写文件时 json.dumps 会产出
    `NaN`/`Infinity` 字面量——这不是合法 JSON（RFC 8259），会污染状态文件，
    因此非有限值也视为坏点剔除。
    """
    if not isinstance(point, dict):
        return False
    try:
        t = float(point.get("t", 0.0))
        p = float(point.get("p", 0.0))
    except (TypeError, ValueError, OverflowError):
        # OverflowError：R2-M-01，t/p 为 400 位整数字面量时 float() 溢出
        return False
    return math.isfinite(t) and math.isfinite(p)


class HistoryStore:
    """价格历史（用于迷你走势图），每个品种+单位最多保留 max_points 条。

    v0.5 起上限由 120 提升到 1440：按 60 秒刷新计算约覆盖 24 小时，
    这样走势图的「今日」「全部」范围才有足够数据点。
    """

    def __init__(self, path: Path, max_points: int = 1440):
        self.path = Path(path)
        self.max_points = max_points
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # R2-M-05：append 是「读-改-写」整个文件，无锁时多线程并发会互相覆盖
        # （丢更新）甚至交错写出坏文件；生产虽是 UI 单线程写，锁的成本可忽略
        self._lock = threading.Lock()

    def load(self) -> dict[str, list[dict]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            # RecursionError：R2-M-03，超深嵌套的 history.json 不让炸穿刷新链路
            return {}
        if not isinstance(data, dict):
            return {}
        # P2-03：逐项容错——坏点直接剔除（与 settings 的 sanitize 同等对待），
        # 不让脏数据进内存、炸掉刷新渲染与 append 写入链路。
        cleaned: dict[str, list[dict]] = {}
        for name, series in data.items():
            if not isinstance(series, list):
                continue
            points = [pt for pt in series if _valid_point(pt)]
            if len(points) != len(series):
                logger.warning(
                    "history: %s 序列 %s 剔除 %d 条坏数据点",
                    self.path.name,
                    name,
                    len(series) - len(points),
                )
            if points:
                cleaned[name] = points
        return cleaned

    def append(self, metal: str, unit: str, price: float) -> list[dict]:
        # R2-M-05：读-改-写全程持锁，消除并发 append 的丢更新与写坏文件
        with self._lock:
            data = self.load()
            series = data.get(key(metal, unit), [])
            # M-04：非有限价格（NaN/Infinity）不落盘——json.dumps 会把它们写成
            # 非法 JSON 字面量，污染 history.json（下游 json.loads 虽宽容，其他解析器不认）。
            if not math.isfinite(price):
                return series
            now = time.time()
            # R5-BUG-1：series 刚由 load() 读出，点里的 t 可能是「数值字符串」
            # （`_valid_point` 明确放行 "1700000000" 这种形态，因为下游渲染都要
            # 转 float）。但这里直接拿原生值参与减法：`now - "1700000000"` 抛
            # TypeError，异常从 UI 刷新槽 _update_spark 一路逃逸，击穿刷新链路
            # （实测：手改 history.json 一个 t 为字符串，刷新即崩）。
            # 用 safe float 收敛后再比较，坏值按 0 处理（等价于「上一分钟之外」，
            # 走 append 分支，语义与 _valid_point 的既有容错一致）。
            last_t = _num(series[-1].get("t") if series else None, 0.0) if series else 0.0
            if series and now - last_t < 60:
                series[-1] = {"t": now, "p": price}
            else:
                series.append({"t": now, "p": price})
            series = series[-self.max_points :]
            data[key(metal, unit)] = series
            try:
                _atomic_write_text(self.path, json.dumps(data, ensure_ascii=False))
            except OSError:
                pass
            return series

    def series(self, metal: str, unit: str) -> list[dict]:
        return self.load().get(key(metal, unit), [])
