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

from . import __version__
from .config import METAL_NAMES, Config, app_dir, asset_dir
from .price import PriceService, HistoryStore
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    log = logging.getLogger("gold_pet")
    log.info("黄金桌宠 %s 启动", __version__)

    cfg = Config.load()
    if args.metal:
        cfg.set("metal", args.metal)
    if args.unit:
        cfg.set("unit", args.unit)

    if args.once:
        return run_once(args, cfg)

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

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
    log.info("界面已启动")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
