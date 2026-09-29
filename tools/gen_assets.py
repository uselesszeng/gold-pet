"""生成桌宠素材：金娘（戴王冠的二次元少女）各状态帧 + 应用图标。

运行：python tools/gen_assets.py
想改配色/表情，改下面的常量或对应的 draw_* 函数后重跑即可。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance

SIZE = 160
OUT_DIR = Path(__file__).resolve().parent.parent / "assets"
PET_DIR = OUT_DIR / "pet"

# ---- 配色 ----
HAIR = (247, 215, 116)        # 金发主色
HAIR_SHADE = (214, 165, 60)   # 金发暗部
HAIR_LIGHT = (255, 244, 198)  # 金发高光
SKIN = (255, 227, 200)
SKIN_SHADE = (240, 197, 165)
DRESS = (255, 250, 235)
DRESS_TRIM = (233, 194, 78)
CROWN = (255, 211, 77)
CROWN_EDGE = (192, 138, 22)
GEM_RED = (255, 107, 107)
GEM_BLUE = (120, 190, 255)
EYE_WHITE = (255, 255, 255)
IRIS_DARK = (150, 96, 30)
IRIS_MID = (243, 193, 78)
IRIS_LIGHT = (255, 232, 160)
LASH = (72, 48, 24)
BLUSH = (255, 150, 160, 120)
INK = (80, 52, 28)
TEAR = (110, 185, 240)

HEAD_CX, HEAD_CY, HEAD_R = 80, 62, 34


def draw_hair_back(draw, sway: int = 0):
    """后发 + 双马尾 + 发圈。"""
    draw.ellipse([40, 24, 120, 108], fill=HAIR_SHADE)
    draw.ellipse([44, 28, 116, 104], fill=HAIR)
    for side in (-1, 1):
        x = HEAD_CX + side * 44 + sway
        draw.ellipse([x - 15, 62, x + 15, 118], fill=HAIR_SHADE)
        draw.ellipse([x - 12, 66, x + 12, 114], fill=HAIR)
        draw.ellipse([x - 12, 60, x + 12, 72], fill=CROWN)
        draw.ellipse([x - 12, 60, x + 12, 72], outline=CROWN_EDGE, width=2)


def draw_body(draw):
    """连衣裙 + 手臂。"""
    draw.polygon([(58, 92), (102, 92), (114, 150), (46, 150)], fill=DRESS)
    draw.polygon([(58, 92), (102, 92), (106, 112), (54, 112)], fill=DRESS_TRIM)
    draw.arc([56, 84, 104, 104], 200, 340, fill=CROWN_EDGE, width=3)
    draw.ellipse([44, 96, 60, 124], fill=SKIN)
    draw.ellipse([100, 96, 116, 124], fill=SKIN)


def draw_head(draw):
    draw.ellipse(
        [HEAD_CX - HEAD_R, HEAD_CY - HEAD_R, HEAD_CX + HEAD_R, HEAD_CY + HEAD_R],
        fill=SKIN,
    )
    draw.ellipse([70, 88, 90, 100], fill=SKIN_SHADE)


def draw_bangs(draw):
    """刘海、鬓发与头顶呆毛。"""
    draw.ellipse([44, 22, 116, 60], fill=HAIR)
    draw.polygon([(46, 30), (114, 30), (114, 52), (74, 66), (46, 52)], fill=HAIR)
    draw.ellipse([56, 30, 104, 44], fill=HAIR_LIGHT)
    for side in (-1, 1):
        x = HEAD_CX + side * 34
        draw.rounded_rectangle([x - 9, 44, x + 9, 104], radius=8, fill=HAIR)
    draw.line([(HEAD_CX + 2, 26), (HEAD_CX + 10, 10)], fill=HAIR, width=5)
    draw.ellipse([HEAD_CX + 6, 6, HEAD_CX + 14, 14], fill=HAIR)


def draw_crown(draw):
    """小王冠（三尖 + 宝石）。"""
    pts = [(62, 30), (68, 12), (75, 24), (82, 8), (89, 24), (96, 12), (102, 30)]
    draw.polygon(pts, fill=CROWN)
    draw.line(pts + [pts[0]], fill=CROWN_EDGE, width=3, joint="curve")
    draw.rectangle([60, 30, 104, 37], fill=CROWN, outline=CROWN_EDGE, width=2)
    draw.ellipse([78, 31, 86, 39], fill=GEM_RED)
    draw.ellipse([64, 31, 70, 37], fill=GEM_BLUE)
    draw.ellipse([94, 31, 100, 37], fill=GEM_BLUE)


def draw_eyes(draw, mode: str, look: int = 0):
    """眼睛：open / blink / happy / sad / glance / closed。"""
    y = 70
    for side in (-1, 1):
        x = HEAD_CX + side * 20
        if mode in ("blink", "closed"):
            draw.arc([x - 12, y - 6, x + 12, y + 10], 20, 160, fill=LASH, width=4)
            continue
        if mode == "happy":
            draw.arc([x - 12, y - 8, x + 12, y + 10], 200, 340, fill=LASH, width=4)
            continue
        draw.ellipse([x - 12, y - 12, x + 12, y + 10], fill=EYE_WHITE)
        off = look * 3
        draw.ellipse([x - 9 + off, y - 11, x + 9 + off, y + 9], fill=IRIS_DARK)
        draw.ellipse([x - 7 + off, y - 9, x + 7 + off, y + 7], fill=IRIS_MID)
        draw.ellipse([x - 4 + off, y - 6, x + 4 + off, y + 5], fill=IRIS_LIGHT)
        draw.ellipse([x - 4 + off, y - 6, x + 4 + off, y + 4], fill=(90, 55, 25))
        draw.ellipse([x - 7 + off, y - 10, x - 2 + off, y - 5], fill=(255, 255, 255, 235))
        draw.ellipse([x + 2 + off, y + 1, x + 6 + off, y + 5], fill=(255, 255, 255, 180))
        draw.arc([x - 13, y - 14, x + 13, y + 4], 200, 340, fill=LASH, width=4)
        if mode == "sad":
            draw.arc([x - 12, y - 24, x + 12, y - 10], 20, 160, fill=HAIR_SHADE, width=3)
        else:
            draw.arc([x - 12, y - 26, x + 12, y - 12], 200, 340, fill=HAIR_SHADE, width=3)


def draw_mouth(draw, mode: str):
    y = 88
    cx = HEAD_CX
    if mode == "smile":
        draw.arc([cx - 8, y - 2, cx + 8, y + 10], 20, 160, fill=INK, width=3)
    elif mode == "big_smile":
        draw.arc([cx - 11, y - 4, cx + 11, y + 14], 10, 170, fill=INK, width=3)
        draw.arc([cx - 7, y + 2, cx + 7, y + 12], 20, 160, fill=(255, 160, 160), width=2)
    elif mode == "frown":
        draw.arc([cx - 8, y + 4, cx + 8, y + 16], 200, 340, fill=INK, width=3)
    elif mode == "o":
        draw.ellipse([cx - 5, y, cx + 5, y + 10], outline=INK, width=3)
    elif mode == "flat":
        draw.line([cx - 6, y + 6, cx + 6, y + 6], fill=INK, width=3)


def draw_blush(draw):
    for side in (-1, 1):
        x = HEAD_CX + side * 26
        draw.ellipse([x - 9, 78, x + 9, 90], fill=BLUSH)


def draw_sparkles(draw, phase: int):
    for i, (dx, dy) in enumerate([(-52, -18), (52, -14), (-48, 34), (50, 38)]):
        if (i + phase) % 2 == 0:
            x, y = HEAD_CX + dx, HEAD_CY + dy
            draw.line([x - 6, y, x + 6, y], fill=(255, 220, 120, 235), width=3)
            draw.line([x, y - 6, x, y + 6], fill=(255, 220, 120, 235), width=3)


def draw_thinking(draw, phase: int):
    for i in range(3):
        x = HEAD_CX - 14 + i * 14
        y = 14 - (3 if (i + phase) % 2 == 0 else 0)
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(120, 120, 120, 220))


def draw_sleep(draw):
    x, y = 118, 14
    draw.line([x, y, x + 14, y], fill=(110, 110, 110, 220), width=3)
    draw.line([x + 14, y, x, y + 14], fill=(110, 110, 110, 220), width=3)
    draw.line([x, y + 14, x + 14, y + 14], fill=(110, 110, 110, 220), width=3)


def draw_hearts(draw, phase: int):
    """飘起来的小爱心（摸头 / 撒娇时出现）。"""
    spots = [(-46, -22), (46, -18), (-34, 30), (38, 34)]
    for i, (dx, dy) in enumerate(spots):
        if (i + phase) % 2 != 0:
            continue
        x, y = HEAD_CX + dx, HEAD_CY + dy - phase * 4
        draw.ellipse([x - 7, y - 5, x - 1, y + 1], fill=(255, 130, 160, 235))
        draw.ellipse([x + 1, y - 5, x + 7, y + 1], fill=(255, 130, 160, 235))
        draw.polygon([(x - 7, y - 1), (x + 7, y - 1), (x, y + 8)], fill=(255, 130, 160, 235))


def make_frame(state: str, idx: int) -> Image.Image:
    bounce, sway = 0, 0
    eyes, mouth, look = "open", "smile", 0

    if state == "pat":
        bounce = [0, -3][idx % 2]
        eyes, mouth = "blink", "big_smile"
    elif state == "shy":
        bounce = [1, -1, 2][idx % 3]
        eyes, mouth = "glance", "o"
        look = [-1, 1, 0][idx % 3]
    elif state == "idle":
        bounce = [0, -2, -3, -1][idx % 4]
        sway = [0, 1, 0, -1][idx % 4]
        eyes = "blink" if idx % 4 == 2 else "open"
    elif state == "up":
        bounce = [0, -5][idx % 2]
        eyes, mouth = "happy", "big_smile"
    elif state == "down":
        bounce = [2, 3][idx % 2]
        eyes, mouth = "sad", "frown"
    elif state == "loading":
        eyes, mouth = "glance", "o"
        look = -1 if idx % 2 == 0 else 1
    else:  # offline
        eyes, mouth = "closed", "flat"

    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer, "RGBA")
    draw_hair_back(ld, sway)
    draw_body(ld)
    draw_head(ld)
    draw_bangs(ld)
    draw_crown(ld)
    draw_eyes(ld, eyes, look)
    draw_mouth(ld, mouth)
    if state != "offline":
        draw_blush(ld)
    if state == "up":
        draw_sparkles(ld, idx)
    if state == "down":
        for side in (-1, 1):
            x = HEAD_CX + side * 20
            ld.ellipse([x + 6, 78, x + 12, 92], fill=TEAR)
    if state == "loading":
        draw_thinking(ld, idx)
    if state == "offline":
        draw_sleep(ld)
    if state in ("pat", "shy"):
        # 摸头 / 撒娇：加强腮红 + 飘爱心
        draw_blush(ld)
        draw_blush(ld)
        draw_hearts(ld, idx)

    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    img.alpha_composite(layer, (0, bounce))
    if state == "offline":
        img = ImageEnhance.Color(img.convert("RGBA")).enhance(0.12)
    return img


def main() -> None:
    PET_DIR.mkdir(parents=True, exist_ok=True)
    plan = {"idle": 4, "up": 2, "down": 2, "pat": 2, "shy": 3,
            "loading": 2, "offline": 1}
    for state, count in plan.items():
        for i in range(count):
            make_frame(state, i).save(PET_DIR / f"{state}_{i}.png")
            print("生成", PET_DIR / f"{state}_{i}.png")

    icon = make_frame("idle", 0).resize((256, 256), Image.LANCZOS)
    icon.save(OUT_DIR / "icon.png")
    icon.save(
        OUT_DIR / "icon.ico",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("生成", OUT_DIR / "icon.png", OUT_DIR / "icon.ico")


if __name__ == "__main__":
    main()
