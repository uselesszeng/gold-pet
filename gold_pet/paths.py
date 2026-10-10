r"""路径管理：区分「用户数据目录」与「程序资源目录」。

- 用户数据（配置 / 缓存 / 历史 / 日志）：写入用户目录，打包后也不会被覆盖
  Windows: %APPDATA%\GoldPet    其他: ~/.goldpet
- 程序资源（动画帧 / 图标）：随代码或随打包产物分发，只读
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "GoldPet"

# 打包（PyInstaller onefile）后资源解压在 _MEIPASS
_FROZEN_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def app_dir() -> Path:
    """用户数据目录（不存在则创建）。"""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
        path = base / APP_NAME
    else:
        path = Path.home() / f".{APP_NAME.lower()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def asset_dir() -> Path:
    """程序资源目录（源码包内或打包临时目录内的 assets）。"""
    return _FROZEN_BASE / "assets"


def settings_path() -> Path:
    return app_dir() / "settings.json"


def cache_path() -> Path:
    return app_dir() / "last_price.json"


def history_path() -> Path:
    return app_dir() / "history.json"


def alert_history_path() -> Path:
    """提醒历史（v0.5 提醒中心）。"""
    return app_dir() / "alert_history.json"


def log_path() -> Path:
    return app_dir() / "gold-pet.log"


def lock_path() -> Path:
    return app_dir() / ".gold-pet.lock"


def control_info_path() -> Path:
    """AI 控制服务发现文件（v0.8）：port/token/pid，供 MCP 桥接进程读取。"""
    return app_dir() / "control.json"
