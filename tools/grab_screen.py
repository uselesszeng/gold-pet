"""截图脚本：截取屏幕右下角区域，用于确认桌宠是否可见。

用法：python tools/grab_screen.py [输出路径]
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()


def find_pet_window() -> int | None:
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    hits: list[int] = []

    def cb(hwnd, _lparam):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if "黄金桌宠" in buf.value and user32.IsWindowVisible(hwnd):
                hits.append(int(hwnd))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return hits[0] if hits else None


def grab(left: int, top: int, width: int, height: int, out: Path) -> bool:
    hwnd_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    gdi32.SelectObject(mem_dc, bmp)
    gdi32.BitBlt(mem_dc, 0, 0, width, height, hwnd_dc, left, top, 0x00CC0020)  # SRCCOPY

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD),
            ("biWidth", wt.LONG),
            ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD),
            ("biBitCount", wt.WORD),
            ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", wt.LONG),
            ("biYPelsPerMeter", wt.LONG),
            ("biClrUsed", wt.DWORD),
            ("biClrImportant", wt.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = width
    bi.bmiHeader.biHeight = -height  # 负值 = 自上而下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0  # BI_RGB

    buf_size = width * height * 4
    buffer = ctypes.create_string_buffer(buf_size)
    gdi32.GetDIBits(mem_dc, bmp, 0, height, buffer, ctypes.byref(bi), 0)

    # BGRA -> 写 BMP 文件（免第三方库）
    row_padded = (width * 4 + 3) & ~3
    pixel_data = bytearray()
    for y in range(height - 1, -1, -1):  # BMP 自下而上
        start = y * width * 4
        pixel_data += buffer.raw[start : start + width * 4]
    if row_padded != width * 4:
        pixel_data += b"\x00" * (row_padded - width * 4)

    file_header = bytearray(b"BM")
    file_header += (14 + 40 + len(pixel_data)).to_bytes(4, "little")
    file_header += (0).to_bytes(4, "little")
    file_header += (54).to_bytes(4, "little")
    info = bytearray(40)
    info[0:4] = (40).to_bytes(4, "little")
    info[4:8] = width.to_bytes(4, "little")
    info[8:12] = height.to_bytes(4, "little")
    info[12:14] = (1).to_bytes(2, "little")
    info[14:16] = (32).to_bytes(2, "little")
    out.write_bytes(bytes(file_header) + bytes(info) + bytes(pixel_data))

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(0, hwnd_dc)
    return True


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("pet_shot.bmp")
    hwnd = find_pet_window()
    if hwnd is None:
        print("未找到可见的黄金桌宠窗口")
        return 1

    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    pad = 60  # 多截一点周边，便于看清它在桌面上的位置
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    left = max(0, r.left - pad)
    top = max(0, r.top - pad)
    right = min(sw, r.right + pad)
    bottom = min(sh, r.bottom + pad)

    print(f"窗口 HWND = {hwnd}")
    print(f"窗口几何 = ({r.left}, {r.top}) - ({r.right}, {r.bottom})")
    print(f"截图区域 = ({left}, {top}) - ({right}, {bottom})  {right - left}x{bottom - top}")

    grab(left, top, right - left, bottom - top, out)
    print(f"已保存：{out.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
