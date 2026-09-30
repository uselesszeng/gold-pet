"""从 dsh-whale-pet 导入鲸鱼娘素材，生成 gold-pet 序列帧与图标。

素材来源：https://github.com/aceice01/dsh-whale-pet （lib/anim/*.gif）
  - 6 个 GIF：192x208，索引透明（无半透明），专业画师绘制的 DeepSeek 鲸鱼娘
  - 许可：非商业使用许可（允许非商业分发，需保留来源致谢与许可声明）
  - 原始版权：社区同人素材（codex-pet-DeepSeek-girl），版权归原作者

处理流程：
  1. ImageSequence 逐帧解包（PIL 会自动应用 GIF 的 disposal，无残影）
  2. 每帧转 RGBA，等比缩放到高 160（LANCZOS）
  3. 贴到 160x160 透明画布水平居中，输出 assets/pet/{state}_{i}.png
  4. 每帧延迟取该 GIF 平均值，打印 STATES 建议表（人工粘贴进 ui.py）
  5. 图标：idle 首帧 → assets/icon.png (256px) + assets/icon.ico (多尺寸)

用法：
  python tools/import_dsh_assets.py <dsh仓库anim目录>
  默认目录：C:/Users/tj-435/AppData/Local/Temp/dsh-whale-pet/lib/anim
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image, ImageSequence

# gold-pet 状态 -> dsh GIF 文件名
STATE_MAP: dict[str, str] = {
    "idle": "idle",        # 待机 → 轻微摇摆
    "up": "jumping",       # 上涨 → 张手跳跃
    "down": "failed",      # 下跌 → 低头沮丧
    "pat": "waving",       # 摸头 → 开心挥手
    "shy": "waiting",      # 害羞 → 双手合十卖萌
    "loading": "running",  # 刷新 → 跑动
    "offline": "waiting",  # 断网 → 期待等待
}

SIZE = 160  # 与 ui.py PET_SIZE / tests/test_assets.py 保持一致
ICON_SIZES = [16, 32, 48, 64, 128, 256]

ROOT = Path(__file__).resolve().parent.parent
PET_DIR = ROOT / "assets" / "pet"
BACKUP_DIR = ROOT / "assets" / "_backup_v055"
DEFAULT_SRC = Path(r"C:/Users/tj-435/AppData/Local/Temp/dsh-whale-pet/lib/anim")


def frames_of(gif_path: Path) -> list[Image.Image]:
    """解出 GIF 的全部帧（RGBA）。PIL 的 seek 已正确处理 disposal，无残影。"""
    im = Image.open(gif_path)
    out: list[Image.Image] = []
    for frame in ImageSequence.Iterator(im):
        out.append(frame.convert("RGBA"))
    return out


def avg_delay(gif_path: Path) -> int:
    """该 GIF 的平均帧延迟（毫秒），用作 STATES 的统一间隔。"""
    im = Image.open(gif_path)
    delays = [f.info.get("duration", 0) for f in ImageSequence.Iterator(im)]
    delays = [d for d in delays if d > 0] or [200]
    return round(sum(delays) / len(delays))


def fit_canvas(frame: Image.Image) -> Image.Image:
    """等比缩放到高 160，贴到 160x160 透明画布水平居中。"""
    w, h = frame.size
    scale = SIZE / h
    new_w = round(w * scale)
    resized = frame.resize((new_w, SIZE), Image.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.alpha_composite(resized, ((SIZE - new_w) // 2, 0))
    return canvas


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not src.is_dir():
        print(f"素材目录不存在：{src}")
        return 1

    # ---- 备份旧素材（旧形象可由 tools/gen_assets.py 重新生成，此处双保险）----
    if PET_DIR.is_dir() and not BACKUP_DIR.exists():
        shutil.copytree(PET_DIR, BACKUP_DIR)
        shutil.copy2(ROOT / "assets" / "icon.png", BACKUP_DIR / "icon.png.bak")
        shutil.copy2(ROOT / "assets" / "icon.ico", BACKUP_DIR / "icon.ico.bak")
        print(f"旧素材已备份到 {BACKUP_DIR.relative_to(ROOT)}")

    PET_DIR.mkdir(parents=True, exist_ok=True)
    for old in PET_DIR.glob("*.png"):
        old.unlink()

    # ---- 解帧输出 ----
    print(f"{'状态':<10}{'GIF':<10}{'帧数':<6}{'平均间隔(ms)':<12}")
    for state, gif_name in STATE_MAP.items():
        gif = src / f"{gif_name}.gif"
        frames = frames_of(gif)
        for i, frame in enumerate(frames):
            fit_canvas(frame).save(PET_DIR / f"{state}_{i}.png", optimize=True)
        print(f"{state:<10}{gif_name + '.gif':<10}{len(frames):<6}{avg_delay(gif):<12}")

    # ---- 图标：idle 首帧 ----
    icon_src = frames_of(src / "idle.gif")[0]
    # idle 站姿上下留白较多，裁掉透明边再缩放，保证头像占满画布
    bbox = icon_src.getbbox()
    cropped = icon_src.crop(bbox)
    side = max(cropped.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(cropped, ((side - cropped.size[0]) // 2, (side - cropped.size[1]) // 2))
    icon = square.resize((256, 256), Image.LANCZOS)
    icon.save(ROOT / "assets" / "icon.png", optimize=True)
    icon.save(
        ROOT / "assets" / "icon.ico",
        sizes=[(s, s) for s in ICON_SIZES],
        append_images=[icon.resize((s, s), Image.LANCZOS) for s in ICON_SIZES[1:]],
    )
    print("图标已更新：assets/icon.png + assets/icon.ico（idle 站姿）")
    print("下一步：把上表间隔粘贴进 gold_pet/ui.py 的 STATES")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
