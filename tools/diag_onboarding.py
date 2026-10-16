"""只读诊断：渲染首启引导对话框并截图存证。

用途：验证 FirstRunDialog 的布局与文案（无需真的触发首次运行）。
运行：python tools/diag_onboarding.py
产出：tools/_out/onboarding_*.png
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

OUT = ROOT / "tools" / "_out"
OUT.mkdir(exist_ok=True)


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from gold_pet.ui import FirstRunDialog

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)

    for tray, name in ((True, "with_tray"), (False, "no_tray")):
        dlg = FirstRunDialog(None, has_tray=tray)
        dlg.show()
        for _ in range(15):
            app.processEvents()
        path = OUT / f"onboarding_{name}.png"
        dlg.grab().save(str(path))
        print(
            f"{name:<10} 尺寸 {dlg.width()}×{dlg.height()}  默认勾选: "
            f"桌面={dlg.wants_desktop()} 开始菜单={dlg.wants_startmenu()} "
            f"自启={dlg.wants_autostart()}  → {path.name}"
        )
        dlg.close()

    print(f"\n截图目录：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
