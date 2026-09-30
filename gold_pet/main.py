"""黄金桌宠入口：python run.py

命令行参数：
  --once            只拉一次行情打印到终端后退出（不启动界面，调试用）
  --metal gold|silver|platinum
  --unit cny_g|usd_oz
  --log-level DEBUG|INFO|WARNING
  --version
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import traceback

from . import __version__, autostart, shortcut
from .config import METAL_NAMES, Config, app_dir, asset_dir
from .price import HistoryStore, PriceService
from .ui import PetWindow, build_tray


def setup_logging(level: str = "INFO") -> None:
    r"""日志同时写控制台和文件（%APPDATA%\GoldPet\gold-pet.log）。"""
    log_dir = app_dir()
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "gold-pet.log", maxBytes=256 * 1024, backupCount=2, encoding="utf-8"
    )
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler, logging.StreamHandler(sys.stdout)],
    )

    def excepthook(exc_type, exc, tb):
        logging.getLogger("gold_pet").error(
            "未捕获异常：\n%s", "".join(traceback.format_exception(exc_type, exc, tb))
        )

    sys.excepthook = excepthook


def _ensure_utf8_stdout() -> None:
    r"""让控制台输出用 UTF-8，避免打包后中文变乱码。

    PyInstaller --noconsole 打包出的 exe 在 Windows 上 sys.stdout.encoding 常为
    cp936(GBK)，打印中文（如 --version）会显示成「»Æ½ð×À³è」。这里显式重设为 UTF-8，
    并在 Windows 上一并切换控制台代码页；失败时静默忽略（不影响主流程）。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="黄金桌宠", description="桌面实时金价挂件")
    p.add_argument("--once", action="store_true", help="只拉取一次行情并打印，不启动界面")
    p.add_argument("--metal", default=None, choices=list(METAL_NAMES), help="品种")
    p.add_argument("--unit", default=None, choices=["cny_g", "usd_oz"], help="单位")
    p.add_argument("--log-level", default="INFO", help="日志级别")
    p.add_argument("--version", action="version", version=f"黄金桌宠 {__version__}")
    return p.parse_args(argv)


def run_once(args: argparse.Namespace, cfg: Config) -> int:
    service = PriceService(app_dir() / "last_price.json")
    snap = service.fetch()
    metal = args.metal or cfg.get("metal", "gold")
    unit = args.unit or cfg.get("unit", "cny_g")
    quote = snap.price_of(metal, unit)
    if quote is None:
        print("获取失败：", snap.error or "无数据")
        return 1
    arrow = "▲" if quote.change > 0 else "▼" if quote.change < 0 else "—"
    print(
        f"{METAL_NAMES.get(metal, metal)} {quote.format_price()} {quote.unit} "
        f"{arrow} {quote.change:+.2f} ({quote.change_pct:+.2f}%) "
        f"高 {quote.high:.2f} 低 {quote.low:.2f}"
    )
    return 0


def _maybe_onboard(window, first_run: bool, log) -> None:
    """首次运行引导：询问是否创建快捷方式（仅打包后的 exe）。

    为什么要做这件事：桌宠不进任务栏是产品设计（Qt.Tool），
    用户双击 exe 后关掉窗口就再也找不到入口了。
    小白用户会认为「程序丢了」，实际它只是回到托盘里。

    源码运行（开发模式）跳过：此时快捷方式指向 pythonw.exe + run.py，
    对用户没有意义，只会在桌面留下垃圾。
    """
    if not first_run or not getattr(sys, "frozen", False):
        return
    if not shortcut.is_supported():
        return

    from .ui import FirstRunDialog

    tray_ok = window.tray_icon() is not None
    dlg = FirstRunDialog(window, has_tray=tray_ok)
    # exec() 返回非 0 表示点了「好呀」；用户选「暂时不用」则什么都不做
    if dlg.exec() == 0:
        return

    icon = asset_dir() / "icon.ico"
    wheres = []
    if dlg.wants_desktop():
        wheres.append("desktop")
    if dlg.wants_startmenu():
        wheres.append("startmenu")
    if wheres:
        results = shortcut.create_many(wheres, icon=icon if icon.exists() else None)
        log.info("首启引导已创建快捷方式：%s", results)
    if dlg.wants_autostart():
        autostart.set_enabled(True)
        window.cfg.set("autostart", True)
        window.cfg.save()
        log.info("首启引导已开启开机自启")


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = parse_args(argv)
    setup_logging(args.log_level)
    log = logging.getLogger("gold_pet")
    log.info("黄金桌宠 %s 启动", __version__)

    data_dir = app_dir()
    first_run = not (data_dir / "settings.json").exists()
    cfg = Config.load()
    if args.metal:
        cfg.set("metal", args.metal)
    if args.unit:
        cfg.set("unit", args.unit)

    if args.once:
        return run_once(args, cfg)

    from PySide6.QtCore import QLockFile
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    # 单实例保护：已经有一个金娘在跑就直接退出（业界通用做法：文件锁）
    lock = QLockFile(str(app_dir() / ".gold-pet.lock"))
    lock.setStaleLockTime(30 * 1000)
    if not lock.tryLock(100):
        log.warning("黄金桌宠已在运行，本次启动退出（可在托盘里找到它）")
        print("黄金桌宠已经在运行了，去托盘里找找看～")
        return 0

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("黄金桌宠")
    app.setApplicationDisplayName(f"黄金桌宠 {__version__}")

    icon_path = asset_dir() / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    data_dir = app_dir()
    service = PriceService(data_dir / "last_price.json")
    history = HistoryStore(data_dir / "history.json")

    window = PetWindow(cfg, service, history)
    build_tray(window, cfg)
    window.show()
    if first_run:
        window.welcome()  # 首次运行：说句话 + 托盘气泡引导
        _maybe_onboard(window, first_run, log)  # 首启引导：快捷方式
    log.info("界面已启动")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
