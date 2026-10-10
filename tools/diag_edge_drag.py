"""只读诊断：在真实屏幕上模拟「拖到边缘」，并截图存证。

用途：验证 v0.5.3 的边缘夹取修复在真实多屏/真实几何下生效。
运行：python tools/diag_edge_drag.py
产出：tools/_out/edge_*.png
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.pop("QT_QPA_PLATFORM", None)  # 用真实平台插件

OUT = ROOT / "tools" / "_out"
OUT.mkdir(exist_ok=True)


def main() -> int:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from gold_pet.config import Config
    from gold_pet.price import HistoryStore, PriceService
    from gold_pet.ui import PetWindow

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)

    tmp = ROOT / "tools" / "_out"
    cfg = Config()
    win = PetWindow(
        cfg,
        PriceService(tmp / "last.json"),
        HistoryStore(tmp / "history.json"),
        auto_refresh=False,
        notifier_history_path=tmp / "alert_history.json",
    )
    win.show()
    for _ in range(30):
        app.processEvents()

    screen = win.screen().availableGeometry()
    print(f"屏幕可用区 : {screen.left()},{screen.top()} {screen.width()}×{screen.height()}")
    print(f"窗口尺寸   : {win._window_size()}")

    def drag_to(gx: int, gy: int) -> None:
        off = QPoint(20, 20)

        def ev(kind, x, y):
            return QMouseEvent(
                kind, QPointF(20, 20), QPointF(x, y), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
            )

        win.mousePressEvent(ev(QEvent.MouseButtonPress, win.x() + off.x(), win.y() + off.y()))
        win.mouseMoveEvent(ev(QEvent.MouseMove, gx, gy))
        for _ in range(5):
            app.processEvents()

    cases = [
        ("right", screen.right() + 400, screen.top() + 300),
        ("bottom", screen.left() + 300, screen.bottom() + 400),
        ("bottomright", screen.right() + 400, screen.bottom() + 400),
        ("topleft", screen.left() - 400, screen.top() - 400),
    ]

    ok = True
    for name, gx, gy in cases:
        drag_to(gx, gy)
        geo = win.frameGeometry()
        inside = (
            geo.left() >= screen.left()
            and geo.top() >= screen.top()
            and geo.right() <= screen.right()
            and geo.bottom() <= screen.bottom()
        )
        ok = ok and inside
        print(
            f"拖到 {name:<12} → 窗口 {geo.left():>5},{geo.top():>5} "
            f"右{geo.right():>5} 下{geo.bottom():>5}  {'✅ 在屏内' if inside else '❌ 越界'}"
        )
        win.grab().save(str(OUT / f"edge_{name}.png"))

    print("=" * 52)
    print("结论：", "全部夹取成功，无「卡半边」" if ok else "仍有越界，需排查")
    print(f"截图目录：{OUT}")
    win.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
