"""只读诊断：测量桌宠运行时真实资源占用（内存 / 句柄 / 启动时间）。

用途：README「推荐配置 / 资源占用」章节的数据来源，避免凭空估算。
运行：python tools/diag_resources.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def rss_mb(pid: int) -> float:
    """用 tasklist 取 RSS（Windows 无 psutil 依赖）。"""
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not out or "," not in out:
        return -1.0
    parts = [p.strip('"') for p in out.split('","')]
    # 形如 "python.exe","1234","Console","1","123,456 K"
    for token in parts:
        token = token.replace(",", "").replace(" K", "").strip('" ')
        if token.isdigit() and int(token) > 10000:
            return int(token) / 1024
    return -1.0


def main() -> int:
    pid = os.getpid()
    t0 = time.perf_counter()

    from PySide6.QtWidgets import QApplication

    from gold_pet.config import Config
    from gold_pet.price import HistoryStore, PriceService
    from gold_pet.ui import PetWindow

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)

    tmp = ROOT / "_diag_tmp"
    tmp.mkdir(exist_ok=True)

    cfg = Config()
    svc = PriceService(tmp / "last.json")
    hist = HistoryStore(tmp / "history.json")
    win = PetWindow(
        cfg,
        svc,
        hist,
        auto_refresh=False,
        notifier_history_path=tmp / "alert_history.json",
    )
    win.show()
    for _ in range(20):
        app.processEvents()

    boot_ms = (time.perf_counter() - t0) * 1000
    base = rss_mb(pid)

    # 模拟 60 帧动画 + 若干次刷新渲染，观察内存是否稳定
    for _ in range(60):
        win._tick_frame()
        app.processEvents()
    after_anim = rss_mb(pid)

    # 反复重绘走势图 / 气泡（近似刷新 30 次的效果）
    from gold_pet.price import parse_snapshot

    raw = {
        "gds_AUTD": "897.15,0,896.03,897.25,902.00,889.24,15:29:54,900.37,897.00,45270,2,7,2026-09-29,黄金延期".split(
            ","
        ),
        "hf_XAU": "4138.36,4114.93,4138.36,4138.71,4148.85,4113.30,16:37:00,4114.93,4117.01,0,0,0,2026-09-29,伦敦金".split(
            ","
        ),
        "fx_susdcny": "16:37:00,6.7038,6.7042,6.7118,61,6.7094,6.7094,6.7033,6.7042,x,-0.11,0,0,y,0,0,,2026-09-29".split(
            ","
        ),
    }
    snap = parse_snapshot(raw)
    for _ in range(30):
        win._render(snap, silent=True)
        app.processEvents()
    after_render = rss_mb(pid)

    width, height = win._window_size()
    print("=" * 56)
    print("黄金桌宠 资源占用实测（无头 Qt，进程内测量）")
    print("=" * 56)
    print(f"Python        : {sys.version.split()[0]}")
    print(f"启动耗时      : {boot_ms:.0f} ms（解释器 + Qt + 窗口构建）")
    print(f"常驻内存 RSS  : {base:.1f} MB   （窗口显示后）")
    print(f"动画 60 帧后  : {after_anim:.1f} MB   （增量 {after_anim - base:+.1f} MB）")
    print(f"重绘 30 次后  : {after_render:.1f} MB   （增量 {after_render - base:+.1f} MB）")
    print(f"窗口尺寸      : {width} × {height} px")
    exe = ROOT / "release" / "GoldPet-v0.5.2-win64.exe"
    if exe.exists():
        print(f"exe 体积      : {exe.stat().st_size / 1024 / 1024:.1f} MB")
    print("=" * 56)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
