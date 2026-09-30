"""诊断脚本：列出当前所有顶层窗口的几何与可见性，用于排查「窗口看不见」。

用法：python tools/diag_window.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000

DWL_USER = 8


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def main() -> int:
    print("=" * 78)
    print("顶层窗口诊断（只列可见窗口与其几何）")
    print("=" * 78)
    print()
    print(f"{'HWND':>10} {'标题':<24} {'可见':<5} {'几何(left,top,right,bottom)':<32} 尺寸")
    print("-" * 78)

    found: list[int] = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def callback(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        visible = bool(user32.IsWindowVisible(hwnd))
        left, top, right, bottom = _rect(hwnd)
        width, height = right - left, bottom - top
        if "黄金" in title or "金娘" in title:
            found.append(hwnd)
            flags = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            print(
                f"{hwnd:>10} {title:<24} {str(visible):<5} ({left},{top},{right},{bottom})".ljust(
                    76
                )
                + f" {width}x{height}"
            )
            print(
                f"{'':>10} 样式: TOOLWINDOW={bool(flags & WS_EX_TOOLWINDOW)} "
                f"LAYERED={bool(flags & WS_EX_LAYERED)}"
            )
        return True

    user32.EnumWindows(EnumProc(callback), 0)

    if not found:
        print("未找到标题含「黄金」的顶层窗口。")
        print("=> 程序可能未启动，或窗口尚未创建。")
        return 1

    print()
    print("=" * 78)
    print("屏幕信息")
    print("=" * 78)
    user32.SetProcessDPIAware()
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
    print(f"主屏分辨率：{screen_w} x {screen_h}")
    for hwnd in found:
        left, top, right, bottom = _rect(hwnd)
        out_of_view = right <= 0 or bottom <= 0 or left >= screen_w or top >= screen_h
        print()
        print(f"窗口 {hwnd}：")
        print(f"  是否在屏幕可见范围内：{'否 —— 位置在屏幕外！' if out_of_view else '是'}")
        if out_of_view:
            print("  => 修复：把 settings.json 的 pos 改成 null，或删除该字段")
    return 0


if __name__ == "__main__":
    sys.exit(main())
