r"""黄金桌宠无头 CLI（v0.8）：供智能体/脚本在本机操控鲸娘，不拉起 GUI。

用法：
    python cli.py <command> [args] [--json]

通信方式（以实际代码为准的选择）：
- 目标是 gold_pet/control_server.py 的本地 HTTP 控制服务，只绑定 127.0.0.1
  随机高位端口 + token 鉴权，接入信息（port/token/pid）写在发现文件
  control.json（%APPDATA%\GoldPet\，仅本机当前用户可读）。
- 没有走 tools/pet_mcp_server.py 的 MCP stdio 桥：该桥依赖第三方 mcp/httpx，
  且工具返回人类可读文案（丢失结构化信息，无法支撑 --json 输出与退出码）。
  CLI 直接用标准库 urllib 调 HTTP，发现机制与 MCP 桥同源（同一个 control.json），
  零新增依赖。
- price / history / alerts 没有对应 HTTP 端点（服务路由只有
  /status /say /play /pat /tease /move），这三个命令只读访问应用落盘的
  数据文件（last_price.json / history.json / alert_history.json），
  复用 gold_pet.price / gold_pet.config 的同源容错逻辑，不写任何文件。

安全边界：
- 目标地址硬编码 127.0.0.1，不接受 --host 等参数指向其它地址；端口与 token
  只能来自本机用户目录下的发现文件。
- say 文本在 CLI 侧先做与服务端同源的校验（去空白 + 500 字上限）再发送；
  HTML 转义统一在应用渲染边界 bubble.render_line_html 完成，CLI 不另行转义。
- 命令到端点的映射是固定白名单，不提供任意 HTTP 方法/路径透传。

退出码：
    0 成功
    2 用法错误（未知命令、缺参数等 argparse 错误）
    3 入参校验失败（空文本、非法动画状态、非法 limit 等）
    4 运行时失败（桌宠未运行 / 服务不可达 / 响应异常 / 数据文件不可读）

--json 模式：stdout 只输出一行 JSON：
    {"ok": true,  "command": "...", "data": {...},  "durationMs": 12}
    {"ok": false, "command": "...", "error": {"code": "...", "message": "..."}, "durationMs": 3}
日志与提示一律走 stderr；默认（不带 --json）输出人类可读文本。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# gold_pet.paths / config / price 均为纯标准库实现，可在无 GUI 环境导入；
# 刻意不导入 gold_pet.control_server（它顶层 import PySide6）。
from gold_pet import __version__
from gold_pet import price as price_mod
from gold_pet.config import METAL_NAMES, UNIT_NAMES, Config
from gold_pet.paths import alert_history_path, cache_path, control_info_path, history_path

# ---- 控制协议常量（REVIEW5-04）----
# 从 gold_pet.protocol「引用」而非「手抄一份」：该模块是 CLI 与桌宠服务端的
# 唯一事实来源。原先是两处手工同步（靠注释约束），服务端改了而 cli 忘了改
# 就会静默产生两端语义不一致（CLI 放行 600 字、服务端截断到 500）。
# 不 import control_server 的原因不变：那个模块顶层拉 PySide6，而 CLI 要能在
# 无 GUI 环境（纯命令行 / CI）运行。protocol 只含纯常量，零依赖。
from gold_pet.protocol import ALERT_KINDS, MAX_SAY_CHARS, PLAYABLE_STATES

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_VALIDATE = 3
EXIT_RUNTIME = 4

_HTTP_TIMEOUT = 5.0


class CliError(Exception):
    """带退出码的 CLI 错误。exit_code: 3=校验失败 4=运行时失败。"""

    def __init__(self, message: str, exit_code: int = EXIT_RUNTIME, code: str = "runtime"):
        super().__init__(message)
        self.exit_code = exit_code
        self.code = code


# ==================================================================== 发现与 HTTP
def _pid_alive(pid: int) -> bool:
    """进程探活（与服务端 control_server._pid_alive 同源的轻量实现）。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            return (
                bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
                and exit_code.value == STILL_ACTIVE
            )
        finally:
            kernel32.CloseHandle(handle)
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _discover() -> tuple[int, str]:
    """读发现文件 -> (port, token)；桌宠未运行/残留文件都给出明确提示。"""
    try:
        info = json.loads(control_info_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CliError(
            "桌宠没有在运行（未找到接入信息 control.json）。"
            "请先启动黄金桌宠：python run.py（或双击 run.bat），"
            "启动后本命令即可使用。"
        ) from None
    except (OSError, ValueError, RecursionError):
        # RecursionError：R2-M-03，超深嵌套的发现文件按损坏处理
        raise CliError("接入信息文件损坏，请重启黄金桌宠后重试。") from None
    if not isinstance(info, dict):
        raise CliError("接入信息文件格式异常，请重启黄金桌宠后重试。")
    port, token, pid = info.get("port"), info.get("token"), info.get("pid")
    # M-08：发现文件可能被手改坏——port 必须是 1~65535 的 int、token 必须是非空 str，
    # 否则 int("abc") 这类转换会把 ValueError 直接抛到 main 之外炸穿退出码约定。
    if (
        not isinstance(port, int)
        or isinstance(port, bool)
        or not (0 < port < 65536)
        or not isinstance(token, str)
        or not token
    ):
        raise CliError("接入信息不完整，请重启黄金桌宠后重试。")
    # 残留文件（进程已退出）不能拿去连——端口/token 都已失效
    if isinstance(pid, int) and not _pid_alive(pid):
        raise CliError(
            "桌宠没有在运行（control.json 是上次退出残留的）。"
            "请先启动黄金桌宠：python run.py（或双击 run.bat）。"
        )
    return port, token


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    """调用控制服务端点（仅限固定白名单路径，目标锁定 127.0.0.1）。"""
    port, token = _discover()
    url = f"http://127.0.0.1:{port}{path}"  # 地址硬编码回环，绝不指向其它主机
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body if method == "POST" else None, method=method)
    req.add_header("X-GP-Token", token)
    if method == "POST":
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
            message = str(data.get("error", ""))
        except (ValueError, UnicodeDecodeError, OSError):
            message = ""
        if exc.code == 401:
            raise CliError("token 不匹配（桌宠可能重启过），请重试一次。") from None
        raise CliError(message or f"控制服务返回 HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError):
        raise CliError(
            "控制服务没有响应（可能刚退出）。请确认黄金桌宠在运行后重试：python run.py"
        ) from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise CliError(f"控制服务返回了无法解析的内容（HTTP {status}）。") from None
    if not isinstance(data, dict):
        raise CliError("控制服务返回了非对象 JSON。")
    if not data.get("ok"):
        raise CliError(str(data.get("error", f"控制服务返回失败（HTTP {status}）")))
    return data


# ==================================================================== 本地数据
def _fmt_time(ts, fmt: str) -> str:
    """安全格式化时间戳（M-06）：数据文件可能被手改成任意值。

    t=1e30 抛 OverflowError、NaN 抛 ValueError、字符串数字也能被 float() 收下；
    转换/格式化失败一律显示 "?"，绝不让展示层崩穿退出码约定。
    """
    try:
        return time.strftime(fmt, time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"


def _load_json(path, missing_ok: bool = False):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if missing_ok:
            return None
        raise CliError(f"数据文件不存在：{path}（桌宠可能尚未产生该数据）") from None
    except (OSError, ValueError, RecursionError):
        # RecursionError：R2-M-03，超深嵌套的数据文件按损坏处理，不裸 traceback
        raise CliError(f"数据文件损坏或不可读：{path}") from None


def _load_config() -> Config:
    return Config.load()


def _valid_point(point: object) -> bool:
    """P2-03 同源容错：t/p 均可转 float 才算有效点（与 price._valid_point 一致）。"""
    return price_mod._valid_point(point)


# ==================================================================== 子命令
def cmd_status(args) -> dict:
    data = _request("GET", "/status")
    cfg = _load_config()
    # 配置摘要 + 最新价快照（本地只读，失败不阻塞 status 主结果）
    summary = {
        "metal": cfg.get("metal"),
        "metal_name": METAL_NAMES.get(cfg.get("metal"), "?"),
        "unit": cfg.get("unit"),
        "unit_name": UNIT_NAMES.get(cfg.get("unit"), "?"),
        "interval_sec": cfg.get("interval_sec"),
        "activity_level": cfg.get("activity_level"),
        "control_enabled": bool(cfg.get("control_enabled", True)),
        "alert_pct": cfg.get("alert_pct"),
    }
    try:
        snap = price_mod.Snapshot.from_dict(_load_json(cache_path()))
        quote = snap.price_of(cfg.get("metal", "gold"), cfg.get("unit", "cny_g"))
        summary["price"] = (
            {
                "price": quote.price,
                "change_pct": round(quote.change_pct, 3),
                "unit": quote.unit,
                "name": quote.name,
                "time_text": quote.time_text,
                "snapshot_ok": snap.ok,
            }
            if quote is not None
            else None
        )
    except CliError:
        summary["price"] = None
    data["config"] = summary
    return data


def cmd_say(args) -> dict:
    text = (args.text or "").strip() if args.text else ""
    mood = (args.mood or "").strip() if args.mood else ""
    if text and mood:
        raise CliError("text 与 mood 只能二选一。", EXIT_VALIDATE, "validation")
    if text:
        # P2-01 同源校验：与服务端一致的限长（转义在应用渲染层统一做）
        text = text[:MAX_SAY_CHARS]
        resp = _request("POST", "/say", {"text": text})
    elif mood:
        resp = _request("POST", "/say", {"mood": mood})
    else:
        raise CliError(
            "需要非空的 text（自由发言）或 --mood（台词组）。", EXIT_VALIDATE, "validation"
        )
    return {"mode": resp.get("mode", "text"), "length": len(text) if text else None}


def cmd_play(args) -> dict:
    if args.state not in PLAYABLE_STATES:
        raise CliError(
            f"state 必须是 {'/'.join(PLAYABLE_STATES)} 之一。", EXIT_VALIDATE, "validation"
        )
    _request("POST", "/play", {"state": args.state})
    return {"state": args.state}


def cmd_pat(_args) -> dict:
    _request("POST", "/pat")
    return {}


def cmd_tease(_args) -> dict:
    _request("POST", "/tease")
    return {}


def cmd_move(args) -> dict:
    if args.dx is not None and args.dy is not None:
        payload: dict = {"dx": args.dx, "dy": args.dy}
    elif args.x is not None and args.y is not None:
        payload = {"x": args.x, "y": args.y}
    else:
        raise CliError(
            "需要 --dx/--dy（相对挪动）或 --x/--y（绝对坐标）。", EXIT_VALIDATE, "validation"
        )
    _request("POST", "/move", payload)
    return payload


def cmd_price(args) -> dict:
    cfg = _load_config()
    metal = args.metal or cfg.get("metal", "gold")
    unit = args.unit or cfg.get("unit", "cny_g")
    if metal not in METAL_NAMES:
        raise CliError(f"metal 必须是 {'/'.join(METAL_NAMES)} 之一。", EXIT_VALIDATE, "validation")
    available_units = price_mod.METALS[metal]["sources"]
    if unit not in available_units:
        raise CliError(
            f"{METAL_NAMES[metal]}支持的 unit：{'/'.join(available_units)}。",
            EXIT_VALIDATE,
            "validation",
        )
    raw = _load_json(cache_path())
    if not isinstance(raw, dict):
        raise CliError("价格缓存格式异常，请等桌宠下次刷新行情。")
    snap = price_mod.Snapshot.from_dict(raw)
    quote = snap.price_of(metal, unit)
    if quote is None:
        raise CliError("价格缓存里没有该品种的行情，请等桌宠下次刷新。")
    if not snap.ok:
        raise CliError(f"最近一次抓取失败：{snap.error or '未知原因'}")
    return {
        "metal": metal,
        "unit": unit,
        "name": quote.name,
        "price": quote.price,
        "prev_close": quote.prev_close,
        "change": round(quote.change, 4),
        "change_pct": round(quote.change_pct, 3),
        "high": quote.high,
        "low": quote.low,
        "time_text": quote.time_text,
        "converted": quote.converted,
        "fx": snap.fx,
        "updated_at": snap.updated_at,
    }


def cmd_history(args) -> dict:
    if args.limit is not None and args.limit < 1:
        raise CliError("--limit 必须是不小于 1 的整数。", EXIT_VALIDATE, "validation")
    limit = args.limit or 60
    raw = _load_json(history_path(), missing_ok=True) or {}
    if not isinstance(raw, dict):
        raise CliError("历史数据格式异常。")
    series_filter = None
    if args.metal or args.unit:
        cfg = _load_config()
        metal = args.metal or cfg.get("metal", "gold")
        unit = args.unit or cfg.get("unit", "cny_g")
        if metal not in METAL_NAMES:
            raise CliError(
                f"metal 必须是 {'/'.join(METAL_NAMES)} 之一。", EXIT_VALIDATE, "validation"
            )
        if unit not in price_mod.METALS[metal]["sources"]:
            raise CliError(
                f"{METAL_NAMES[metal]}支持的 unit：{'/'.join(price_mod.METALS[metal]['sources'])}。",
                EXIT_VALIDATE,
                "validation",
            )
        series_filter = price_mod.key(metal, unit)
    out: dict[str, list] = {}
    dropped = 0
    for key, series in raw.items():
        if not isinstance(series, list):
            continue
        # P2-03 同源容错：坏点直接剔除
        points = [pt for pt in series if _valid_point(pt)]
        dropped += len(series) - len(points)
        if series_filter and key != series_filter:
            continue
        out[key] = [{"t": pt["t"], "p": pt["p"]} for pt in points[-limit:]]
    if not out:
        raise CliError("历史记录为空（桌宠可能尚未完成一次行情刷新）。")
    return {"limit": limit, "series": out, "dropped_bad_points": dropped}


def cmd_alerts(args) -> dict:
    if args.limit is not None and args.limit < 1:
        raise CliError("--limit 必须是不小于 1 的整数。", EXIT_VALIDATE, "validation")
    limit = args.limit or 20
    if args.kind and args.kind not in ALERT_KINDS:
        raise CliError(f"--kind 必须是 {'/'.join(ALERT_KINDS)} 之一。", EXIT_VALIDATE, "validation")
    raw = _load_json(alert_history_path(), missing_ok=True)
    items = raw if isinstance(raw, list) else []
    if args.kind:
        items = [a for a in items if isinstance(a, dict) and a.get("kind") == args.kind]
    items = items[-limit:]
    if not items:
        raise CliError("提醒历史为空。")
    return {"limit": limit, "kind": args.kind, "alerts": items, "count": len(items)}


# ==================================================================== 参数解析
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description=f"黄金桌宠无头 CLI v{__version__}：在本机操控鲸娘（不拉起 GUI）。",
        epilog=(
            "示例：\n"
            "  python cli.py status --json\n"
            '  python cli.py say "主人好呀"\n'
            "  python cli.py history --limit 30 --metal gold --unit cny_g\n"
            "注：--json 必须放在子命令之前；控制服务仅在本机 127.0.0.1 可达。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="stdout 只输出 JSON（{ok, command, data|error, durationMs}），日志走 stderr",
    )
    # 让 --json 也能写在子命令之后：SUPPRESS 缺省避免子解析器把全局值覆盖回 False
    _common = argparse.ArgumentParser(add_help=False)
    _common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    p = sub.add_parser(
        "status", parents=[_common], help="桌宠运行状态 / 当前金价 / 配置摘要（走控制服务）"
    )
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "say", parents=[_common], help="让鲸娘说话（CLI 侧先做与服务端同源的限长校验）"
    )
    p.add_argument("text", nargs="?", default="", help="自由发言文本（≤500 字，超长截断）")
    p.add_argument(
        "--mood",
        default="",
        help="从台词组随机挑一句：up/down/idle/pat/shy/welcome/loading/offline/act_*",
    )
    p.set_defaults(func=cmd_say)

    p = sub.add_parser("play", parents=[_common], help="播放一段动作动画（白名单内）")
    p.add_argument("state", help="idle/up/down/pat/shy 之一")
    p.set_defaults(func=cmd_play)

    p = sub.add_parser("pat", parents=[_common], help="摸摸头（傲娇值清零）")
    p.set_defaults(func=cmd_pat)

    p = sub.add_parser("tease", parents=[_common], help="叫她大肥鱼（触发傲娇彩蛋）")
    p.set_defaults(func=cmd_tease)

    p = sub.add_parser("move", parents=[_common], help="挪动鲸娘位置（自动夹取回屏幕内）")
    p.add_argument("--dx", type=int, help="相对水平偏移（像素）")
    p.add_argument("--dy", type=int, help="相对垂直偏移（像素）")
    p.add_argument("--x", type=int, help="绝对横坐标（与 --y 成对使用）")
    p.add_argument("--y", type=int, help="绝对纵坐标（与 --x 成对使用）")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser(
        "price",
        parents=[_common],
        help="读取最新行情快照（本地缓存只读；控制服务无触发刷新的端点）",
    )
    p.add_argument("--metal", help="gold/silver/platinum，缺省读配置")
    p.add_argument("--unit", help="cny_g/usd_oz，缺省读配置（需该品种支持）")
    p.set_defaults(func=cmd_price)

    p = sub.add_parser(
        "history",
        parents=[_common],
        help="读取价格历史（本地 history.json 只读，P2-03 同源容错剔除坏点）",
    )
    p.add_argument("--limit", type=int, default=None, help="每条序列最多返回 N 个点（默认 60）")
    p.add_argument("--metal", help="gold/silver/platinum，缺省读配置")
    p.add_argument("--unit", help="cny_g/usd_oz，缺省读配置")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser(
        "alerts",
        parents=[_common],
        help="读取提醒历史（本地 alert_history.json 只读；无增删端点，不做写操作）",
    )
    p.add_argument(
        "--limit", type=int, default=None, help="最多返回 N 条（默认 20，按时间正序取末尾）"
    )
    p.add_argument("--kind", help="pct/target/extreme 之一")
    p.set_defaults(func=cmd_alerts)

    return parser


# ==================================================================== 主入口
def _print_human(command: str, data: dict) -> None:
    """默认人类可读输出（stdout）。"""
    if command == "status":
        cfg = data.get("config", {})
        price_info = cfg.get("price")
        line = (
            f"鲸娘状态：{data.get('state', '?')}｜活跃度：{data.get('activity_level', '?')}｜"
            f"被叫大肥鱼：{data.get('fatty_count', 0)} 次｜版本：v{data.get('version', '?')}"
        )
        print(line)
        if price_info:
            print(
                f"最新价：{price_info['name']} {price_info['price']} "
                f"（{price_info.get('change_pct', 0):+.2f}%，{price_info.get('time_text', '')}）"
            )
        print(
            f"配置：{cfg.get('metal_name')} / {cfg.get('unit_name')}｜"
            f"刷新间隔 {cfg.get('interval_sec')}s｜AI 控制服务 "
            f"{'开' if cfg.get('control_enabled') else '关'}"
        )
    elif command == "say":
        print("鲸娘说完了～")
    elif command == "play":
        print("鲸娘做完了动作～")
    elif command == "pat":
        print("鲸娘被摸头了，眯眼笑～傲娇值已清零")
    elif command == "tease":
        print("你又叫她大肥鱼了，鲸娘很生气！")
    elif command == "move":
        print("鲸娘挪好位置啦～")
    elif command == "price":
        print(
            f"{data['name']}：{data['price']}（{data['change_pct']:+.2f}%）"
            f"｜最高 {data['high']} 最低 {data['low']}｜{data['time_text']}"
        )
    elif command == "history":
        for key, pts in data["series"].items():
            head = f"{key}（共 {len(pts)} 点，坏点剔除 {data['dropped_bad_points']}）"
            print(head)
            for pt in pts[-5:]:
                print(f"  {_fmt_time(pt['t'], '%m-%d %H:%M:%S')}  {pt['p']}")
            if len(pts) > 5:
                print(f"  …（仅展示最近 5 点，共 {len(pts)} 点）")
    elif command == "alerts":
        print(f"提醒历史 {data['count']} 条：")
        for a in data["alerts"]:
            if not isinstance(a, dict):  # M-06：脏文件里混入非对象项，展示层跳过
                continue
            at = _fmt_time(a.get("at", 0), "%m-%d %H:%M:%S")
            print(f"  [{at}] {a.get('title', '')} {a.get('body', '')}")


def main(argv: list[str] | None = None) -> int:
    # 输出统一 UTF-8：被重定向/管道时 Windows 默认用本地编码（cp936），
    # 智能体按 UTF-8 读会乱码；显式重配置保证任何消费方拿到的都是 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)  # 用法错误 argparse 自行退出 2
    started = time.perf_counter()

    def emit(ok: bool, command: str, payload: dict | str) -> int:
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        if args.json:
            if ok:
                out = {"ok": True, "command": command, "data": payload, "durationMs": duration_ms}
            else:
                out = {
                    "ok": False,
                    "command": command,
                    "error": payload,
                    "durationMs": duration_ms,
                }
            print(json.dumps(out, ensure_ascii=False))
        elif ok:
            _print_human(command, payload)
        else:
            print(f"失败：{payload['message']}", file=sys.stderr)
        return EXIT_OK if ok else payload["exit_code"]

    try:
        data = args.func(args)
    except CliError as exc:
        return emit(
            False,
            args.command,
            {"code": exc.code, "message": str(exc), "exit_code": exc.exit_code},
        )
    except Exception as exc:  # R-monkey 审查兜底：非 CliError 异常也走退出码约定（4）
        return emit(
            False,
            args.command,
            {
                "code": "runtime",
                "message": f"内部错误：{type(exc).__name__}: {exc}",
                "exit_code": EXIT_RUNTIME,
            },
        )
    return emit(True, args.command, data)


if __name__ == "__main__":
    sys.exit(main())
