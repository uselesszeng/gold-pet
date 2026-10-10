"""测量桌宠窗口与各部分的实际尺寸，为边缘约束提供数据依据。

只读诊断，不修改任何配置。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from gold_pet.config import Config  # noqa: E402
from gold_pet.ui import PetWindow  # noqa: E402


class _FakeService:
    def load_cache(self):
        return None

    def fetch(self):  # pragma: no cover - 诊断脚本不联网
        return None


def main() -> int:
    app = QApplication(sys.argv)
    cfg = Config()
    win = PetWindow(cfg, _FakeService(), auto_refresh=False)
    win.show()
    app.processEvents()

    print("=" * 58)
    print("窗口几何")
    print("=" * 58)
    g = win.geometry()
    print(f"  窗口 x,y,w,h      = {g.x()}, {g.y()}, {g.width()}, {g.height()}")
    print(f"  visible           = {win.isVisible()}")
    print(f"  sizeHint          = {win.sizeHint().width()} x {win.sizeHint().height()}")
    print(f"  minimumSizeHint   = {win.minimumSizeHint().width()} x {win.minimumSizeHint().height()}")

    print()
    print("=" * 58)
    print("子部件几何（相对窗口客户区）")
    print("=" * 58)
    for name in ("bubble", "spark", "pet"):
        w = getattr(win, name, None)
        if w is None:
            continue
        r = w.geometry()
        print(
            f"  {name:6s} x={r.x():4d} y={r.y():4d} w={r.width():4d} h={r.height():4d} "
            f"visible={w.isVisible()}"
        )

    print()
    print("=" * 58)
    print("屏幕信息")
    print("=" * 58)
    for i, s in enumerate(QApplication.screens()):
        ag = s.availableGeometry()
        fg = s.geometry()
        print(f"  屏幕{i}: 全屏 {fg.width()}x{fg.height()} @ ({fg.x()},{fg.y()})")
        print(f"          可用 {ag.width()}x{ag.height()} @ ({ag.x()},{ag.y()})")
        print(f"          DPR = {s.devicePixelRatio()}")

    # 模拟：把窗口拖到屏幕最右侧，看会超出多少
    screen = win.screen() or QApplication.primaryScreen()
    if screen is not None:
        ag = screen.availableGeometry()
        print()
        print("=" * 58)
        print("模拟拖到屏幕最右 / 最下（当前无约束时的情况）")
        print("=" * 58)
        win.move(ag.right() - 20, ag.top())
        app.processEvents()
        g2 = win.geometry()
        overflow_right = g2.right() - ag.right()
        print(f"  移到 x={ag.right() - 20}: 窗口右边界={g2.right()} 屏幕右边界={ag.right()}")
        print(f"    超出右侧 = {overflow_right} px  ← 被裁掉的部分")
        win.move(ag.left(), ag.bottom() - 20)
        app.processEvents()
        g3 = win.geometry()
        print(f"  移到 y={ag.bottom() - 20}: 窗口下边界={g3.bottom()} 屏幕下边界={ag.bottom()}")
        print(f"    超出下侧 = {g3.bottom() - ag.bottom()} px")

    win.hide()
    return 0


if __name__ == "__main__":
    sys.exit(main())
