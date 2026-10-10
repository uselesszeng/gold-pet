"""一键切换应用/托盘图标的形象版本。

形象经历了多个版本，各有拥趸；想用哪版图标就切哪版：

  python tools/switch_icon.py list            列出可用版本
  python tools/switch_icon.py v055            切到金色鲸鱼女仆（v0.5.5 同款）
  python tools/switch_icon.py v060            切到 DeepSeek 鲸鱼娘（当前默认）

原理：把 assets/icons/ 里对应版本的 icon-*.png / .ico 复制为
assets/icon.png / assets/icon.ico（程序与打包脚本读的是这两个固定路径）。

注意：
  - 源码运行：重启程序即生效
  - exe：文件图标在**打包时**嵌入，切换后需重新跑
    `.venv/Scripts/python.exe -m PyInstaller --noconfirm gold-pet.spec`
    才能生成对应图标的新 exe
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICONS_DIR = ROOT / "assets" / "icons"

# 版本号 -> (png 文件名, ico 文件名, 说明)
VERSIONS: dict[str, tuple[str, str, str]] = {
    "v055": (
        "icon-v055-gold.png",
        "icon-v055-gold.ico",
        "金色鲸鱼女仆（v0.5.5~v0.5.6 同款，PIL 自绘）",
    ),
    "v060": (
        "icon-v060-whale.png",
        "icon-v060-whale.ico",
        "DeepSeek 鲸鱼娘（v0.6.0 起默认，画师素材）",
    ),
}


def list_versions() -> None:
    current_found = False
    for name, (png, _ico, desc) in VERSIONS.items():
        mark = (
            "  当前"
            if (ROOT / "assets" / "icon.png").read_bytes() == (ICONS_DIR / png).read_bytes()
            else ""
        )
        if mark:
            current_found = True
        print(f"  {name:<6} {desc}{mark}")
    if not current_found:
        print("  当前图标不匹配任何已归档版本（可能是旧版或其他自定义图标）")


def switch(version: str) -> int:
    if version not in VERSIONS:
        print(f"未知版本 {version!r}。可用：{' / '.join(VERSIONS)} 或 list")
        return 1
    png_name, ico_name, desc = VERSIONS[version]
    shutil.copy2(ICONS_DIR / png_name, ROOT / "assets" / "icon.png")
    shutil.copy2(ICONS_DIR / ico_name, ROOT / "assets" / "icon.ico")
    print(f"已切换到 {version}：{desc}")
    print("  - 源码运行：重启程序生效")
    print("  - exe：需重新打包才能更新文件图标（命令见本文件 docstring）")
    return 0


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else "list"
    if arg == "list":
        list_versions()
        return 0
    return switch(arg)


if __name__ == "__main__":
    raise SystemExit(main())
