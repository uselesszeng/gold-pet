"""黄金桌宠入口：python run.py"""

from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .config import Config, app_dir, asset_dir
from .price import HistoryStore, PriceService
from .ui import PetWindow, build_tray


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("黄金桌宠")

    icon_path = asset_dir() / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    data_dir = app_dir()
    cfg = Config.load()
    service = PriceService(data_dir / "last_price.json")
    history = HistoryStore(data_dir / "history.json")

    window = PetWindow(cfg, service, history)
    build_tray(window, cfg)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
