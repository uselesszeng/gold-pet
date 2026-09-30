"""生成桌宠素材：金娘（鲸鱼拟人 · 金发女仆装）各状态帧 + 应用图标。

运行：python tools/gen_assets.py
想改配色/表情，改下面的常量或对应的 draw_* 函数后重跑即可。

设计要点（v0.5.5 重绘，经 10 轮视觉迭代收敛）
-------------------------------------------
1. **4x 超采样**：所有图形按 ``SIZE*SS`` 画布绘制，最后 LANCZOS 缩到 ``SIZE``，
   消除 PIL 矢量抗锯齿粗糙造成的「像素毛边」。
2. **只用曲线构型**：PIL 的 ``polygon`` 只能画直边，堆叠后必然呈「方框 / 夹子 /
   贴纸」感。因此除裙身梯形与王冠尖外，所有造型元素一律使用
   ``ellipse`` / ``arc`` / ``chord`` 组合实现。
3. **元素减法（关键定稿决策）**：160x160 有效区域约 130x150 px²，实测可容纳的
   「可辨识元素」上限约 4~5 个。早期版本塞入 10 个元素（王冠+头巾+蝴蝶结+呆毛+
   双耳鳍+长发+女仆裙+围裙+小鲸鱼+尾鳍），导致每个元素都不精致。
   最终**删除蝴蝶结与呆毛**，保留 4 个强识别符号：
       - 尾鳍（鲸鱼）
       - 耳鳍（鲸鱼）
       - 头巾（女仆）
       - 围裙小鲸鱼（细节彩蛋）
   释放的空间全部投入面部（眼睛 4 层虹膜 + 双高光 + 上睫毛）。
4. **配色**：主色 ``#F5C518`` 饱和金，强化「黄金桌宠」的品牌识别；
   女仆裙用深藏蓝 + 白围裙，与金发形成强对比。
5. **比例系统（160 逻辑像素基准，Q 版 ~2.2 头身）**：
       y   0 ~ 30   王冠（三尖 + 底带）
       y  22 ~ 66   头巾（白色发带 + 荷叶边）
       y  26 ~ 96   头部（HEAD_R=34）
       y  46 ~ 84   眉眼区（刘海下沿 44~50）
       y  88 ~ 104  脖颈
       y  98 ~ 156  女仆裙 + 围裙 + 小鲸鱼
       y  96 ~ 150  尾鳍（身体右后方）
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance

SIZE = 160  # 输出尺寸（像素）
SS = 4  # 超采样倍率
LOGICAL_SIZE = SIZE * SS  # 实际绘制画布尺寸

OUT_DIR = Path(__file__).resolve().parent.parent / "assets"
PET_DIR = OUT_DIR / "pet"

P = SS  # 坐标缩放因子别名：让下面的数值保持「160 逻辑像素」量级可读


def s(*values: float) -> tuple[int, ...]:
    """把逻辑坐标（160 基准）批量换算为超采样画布坐标。"""
    return tuple(int(round(v * P)) for v in values)


# ---- 配色（饱和金主调 + 鲸鱼蓝点缀）----
GOLD = (245, 197, 24)  # #F5C518 黄金主色（品牌色）
HAIR = GOLD
HAIR_DEEP = (198, 152, 10)  # 暗部 / 发根
HAIR_LIGHT = (255, 229, 118)  # 高光
HAIR_TIP = (255, 220, 96)  # 发尾

SKIN = (255, 231, 207)
SKIN_SHADE = (238, 195, 164)
BLUSH = (255, 148, 158, 118)

DRESS = (43, 58, 92)
DRESS_LIGHT = (60, 80, 124)
APRON = (253, 253, 255)
APRON_SHADE = (224, 230, 242)
TRIM_GOLD = (203, 163, 40)
BOW = (78, 128, 204)
BOW_DEEP = (48, 88, 156)

CROWN = (255, 214, 82)
CROWN_EDGE = (176, 124, 12)
GEM_RED = (255, 107, 107)
GEM_BLUE = (120, 190, 255)

EYE_WHITE = (255, 255, 255)
IRIS_DARK = (30, 72, 138)
IRIS_MID = (76, 160, 234)
IRIS_LIGHT = (176, 222, 255)
PUPIL = (22, 48, 98)
LASH = (58, 44, 40)
EYE_BROW = (196, 148, 60)

INK = (86, 56, 30)
TEAR = (110, 185, 240)

FIN = (74, 116, 186)
FIN_LIGHT = (132, 178, 235)
FIN_DEEP = (40, 68, 122)
# 鳍的高亮描边：深色桌面上没有它，鳍会与壁纸融为一体而「消失」
FIN_EDGE = (206, 228, 252)

# ---- 几何锚点（160 逻辑像素基准）----
HEAD_CX, HEAD_CY, HEAD_R = 80, 60, 34


# ============================================================
# 鲸鱼元素
# ============================================================


def _leaf(d, cx: float, cy: float, rx: float, ry: float, side: int, fill) -> None:
    """画一片朝 left/right 张开的椭圆叶片（鲸鱼鳍的基本形状）。"""
    if side > 0:
        box = s(cx - rx, cy - ry, cx + rx, cy + ry)
        d.chord(box, -90, 90, fill=fill)
    else:
        box = s(cx - rx, cy - ry, cx + rx, cy + ry)
        d.chord(box, 90, 270, fill=fill)


def draw_tail_fin(d, sway: int = 0):
    """身体右后方的大鲸鱼尾鳍（本形象的标志性剪影）。

    实现要点：
    - 尾鳍必须**连成一个整体**：先铺一片覆盖整个尾叶区的底，再叠两叶。
    - **不要用透明色 polygon 去「挖」缺口**：在 RGBA 图层上叠加 (0,0,0,0)
      不会真正擦除像素，只会留下半透明脏边。分叉改用「两叶之间的空隙」
      自然形成 —— 即在底部椭圆之后再画上下两叶，中间留缝。
    - 尺寸受画布约束：右边界 160，故尾尖控制在 x<=152，避免被裁切。
    """
    bx = 98 + sway
    by = 122
    # 1) 尾柄：从裙身右侧伸出，与裙摆咬合
    d.ellipse(s(bx, by - 13, bx + 26, by + 13), fill=FIN)
    # 2) 上叶（朝右上，椭圆叶）
    _leaf(d, bx + 30, by - 16, 16, 13, 1, FIN)
    # 3) 下叶（朝右下，椭圆叶）——两叶之间留出空隙即形成分叉
    _leaf(d, bx + 30, by + 15, 16, 13, 1, FIN)
    # 4) 叶根连接（把叶与尾柄接上，消除断裂）
    d.ellipse(s(bx + 14, by - 20, bx + 32, by - 2), fill=FIN)
    d.ellipse(s(bx + 14, by + 2, bx + 32, by + 20), fill=FIN)
    # 5) 内层亮色（体积感）
    _leaf(d, bx + 28, by - 16, 10, 8, 1, FIN_LIGHT)
    _leaf(d, bx + 28, by + 15, 10, 8, 1, FIN_LIGHT)
    # 6) 叶尖加深
    d.chord(s(bx + 36, by - 28, bx + 50, by - 8), -90, 90, fill=FIN_DEEP)
    d.chord(s(bx + 36, by + 8, bx + 50, by + 28), -90, 90, fill=FIN_DEEP)
    # 7) 高亮描边：深色桌面上没有它，尾鳍会与壁纸融为一体
    for cy, ry in ((by - 16, 13), (by + 15, 13)):
        d.chord(s(bx + 14, cy - ry, bx + 46, cy + ry), -90, 90, outline=FIN_EDGE,
                width=int(1.0 * P))


def draw_ear_fins(d, tilt: int = 0):
    """头两侧的鲸鱼耳鳍：朝斜上方的半椭圆叶片 + 弧形鳍条。

    位置要点：鳍根在**头顶两侧偏外**(HEAD_CY-16)，朝斜上方张开，
    而不是挂在脸颊两侧——否则视觉上会变成「耳罩」。
    """
    for side in (-1, 1):
        bx = HEAD_CX + side * 22  # 鳍根（落在发盖上）
        by = HEAD_CY - 20 + tilt  # 起点偏上，贴着发盖外缘
        # 叶片中心沿斜上方偏移
        lx, ly = bx + side * 12, by - 8
        rx, ry = 11, 15
        _leaf(d, lx, ly, rx, ry, side, FIN)
        # 内层：只做一半，留出鳍条空间
        _leaf(d, lx, ly + 4, rx - 4, ry - 6, side, FIN_LIGHT)
        # 鳍条：从鳍根呈放射状
        for i in range(3):
            t = i / 2
            d.line(
                s(bx + side * 2, by + 1 + t * 8, lx + side * (rx - 1), ly - 5 + t * 12),
                fill=FIN_DEEP,
                width=int(1.1 * P),
            )
        # 鳍缘描边（让叶片边界清晰，不与头发糊在一起）
        d.chord(
            s(lx - rx, ly - ry, lx + rx, ly + ry),
            -90 if side > 0 else 90,
            90 if side > 0 else 270,
            outline=FIN_EDGE,
            width=int(0.9 * P),
        )


def draw_whale_mark(d):
    """围裙上的小鲸鱼剪影（本形象最具识别度的细节）。"""
    cx, cy = 80, 130
    bw, bh = 15, 9
    # 身体（水滴形：椭圆 + 左侧加宽）
    d.ellipse(s(cx - bw, cy - bh, cx + bw - 3, cy + bh), fill=BOW_DEEP)
    d.ellipse(s(cx - bw - 3, cy - bh + 3, cx - bw + 8, cy + bh - 3), fill=BOW_DEEP)
    # 尾鳍（上下两小片）
    d.chord(s(cx + 7, cy - 13, cx + 22, cy - 1), -90, 90, fill=BOW_DEEP)
    d.chord(s(cx + 7, cy + 1, cx + 22, cy + 13), -90, 90, fill=BOW_DEEP)
    # 喷水
    d.arc(s(cx - 8, cy - 18, cx - 2, cy - 10), 200, 340, fill=BOW_DEEP, width=int(1.2 * P))
    d.line(s(cx - 5, cy - 11, cx - 5, cy - 6), fill=BOW_DEEP, width=int(1.2 * P))
    # 眼睛（挖空）
    d.ellipse(s(cx - 8, cy - 4, cx - 4, cy), fill=APRON)
    # 腹部（浅色）
    d.chord(s(cx - 12, cy + 1, cx + 8, cy + 11), 0, 180, fill=(190, 214, 240))


# ============================================================
# 头发
# ============================================================


def draw_hair_back(d, sway: int = 0):
    """后发主体 + 两侧长发束（用椭圆叠出体积，不用直边）。"""
    # 后发主体
    d.ellipse(s(44, 20, 116, 104), fill=HAIR_DEEP)
    d.ellipse(s(47, 24, 113, 100), fill=HAIR)
    # 两侧长发束：外深 → 内亮 → 发尾更亮
    for side in (-1, 1):
        cx = HEAD_CX + side * 40 + sway
        d.ellipse(s(cx - 18, 50, cx + 18, 126), fill=HAIR_DEEP)
        d.ellipse(s(cx - 15, 54, cx + 15, 122), fill=HAIR)
        d.ellipse(s(cx - 12, 86, cx + 12, 124), fill=HAIR_TIP)
        # 发尾卷（弧线开口朝下）
        d.ellipse(s(cx - 11, 112, cx + 11, 132), fill=HAIR_TIP)
        d.arc(s(cx - 11, 112, cx + 11, 132), 20, 160, fill=HAIR_DEEP, width=int(1.3 * P))
        # 发丝（浅色弧线）
        d.arc(s(cx - 9, 66, cx + 4, 112), 250, 20, fill=HAIR_LIGHT, width=int(1 * P))


def draw_bangs(d):
    """齐刘海：3 束弧形发片（全用椭圆，避免方框）。

    高度约束：刘海下沿最深 y=54，给头巾（y 24~40）留出上方可见带，
    否则头巾会被刘海整条盖住。
    """
    # 顶部发盖
    d.ellipse(s(45, 20, 115, 56), fill=HAIR)
    # 三束刘海（下沿 42~50，给眉眼留出呼吸空间）
    for cx, hw, bot in ((58, 11, 44), (80, 13, 50), (102, 11, 44)):
        d.ellipse(s(cx - hw, 22, cx + hw, bot), fill=HAIR)
        d.ellipse(s(cx - hw, 22, cx + hw, bot - 6), fill=HAIR)
    # 束间分缝
    for cx in (69, 91):
        d.arc(s(cx - 5, 24, cx + 5, 46), 20, 160, fill=HAIR_DEEP, width=int(1.3 * P))
    # 刘海高光（弧形）
    d.chord(s(62, 24, 98, 40), 180, 360, fill=HAIR_LIGHT)
    # 鬓发（贴脸侧）
    for side in (-1, 1):
        x = HEAD_CX + side * 28
        d.ellipse(s(x - 8, 30, x + 8, 92), fill=HAIR)
        d.ellipse(s(x - 5, 36, x + 5, 88), fill=HAIR_DEEP)


def draw_headband(d):
    """鲸鱼主题头巾：**一条清晰可见的白色发带**（元素减法后的重点保留项）。

    v0.5.5 定稿：删掉蝴蝶结与呆毛后，头顶只留「王冠 + 头巾」两个符号，
    因此头巾必须做**足够显眼**——用实心弧带 + 下缘荷叶边，
    并把纵向位置抬到 y=22~38（王冠底带在 y=19~30，视觉上首尾相接）。
    """
    # 主带（实心粗弧）
    d.arc(s(48, 22, 112, 66), 200, 340, fill=APRON, width=int(6 * P))
    # 下缘厚度阴影
    d.arc(s(48, 26, 112, 70), 200, 340, fill=APRON_SHADE, width=int(1.5 * P))
    # 荷叶边（小圆，仅填色，不描边）
    for i in range(7):
        t = i / 6
        x = 52 + t * 56
        y = 28 - abs(t - 0.5) * 5
        d.ellipse(s(x - 3, y, x + 3, y + 5), fill=APRON)
    # 头巾在后发上的延伸（两小段，暗示「系在脑后」）
    d.arc(s(40, 24, 56, 56), 250, 20, fill=APRON, width=int(3 * P))
    d.arc(s(104, 24, 120, 56), 160, 290, fill=APRON, width=int(3 * P))


def draw_crown(d):
    """小王冠（三尖 + 宝石）—— 黄金桌宠的品牌符号，保留。

    尖角用**多边形**画真正的三角（chord(180,360) 画出来是半圆「柱子」）。
    冠体宽 58~104，需给左侧蝴蝶结(x≈97~123)留出重叠但不互相覆盖的层次：
    王冠在蝴蝶结之后绘制，因此蝴蝶结被压在下面 —— 这正是我们要的效果。
    """
    # 三尖（真三角形）
    for cx, h, hw in ((67, 13, 8), (81, 19, 9), (95, 13, 8)):
        d.polygon(s(cx - hw, 20, cx, 20 - h, cx + hw, 20), fill=CROWN)
        d.line(s(cx - hw, 20, cx, 20 - h, cx + hw, 20), fill=CROWN_EDGE, width=int(1.5 * P),
               joint="curve")
    # 冠体底带
    d.rounded_rectangle(
        s(58, 19, 104, 30), radius=int(2 * P), fill=CROWN, outline=CROWN_EDGE, width=int(1.7 * P)
    )
    # 宝石：中央红宝 + 两侧蓝宝
    d.ellipse(s(77, 20, 85, 29), fill=GEM_RED)
    d.ellipse(s(78, 21, 81, 24), fill=(255, 205, 205))
    d.ellipse(s(62, 21, 68, 27), fill=GEM_BLUE)
    d.ellipse(s(94, 21, 100, 27), fill=GEM_BLUE)


# ============================================================
# 身体
# ============================================================


def draw_head(d):
    """头部（椭圆，非正圆，略宽以显 Q 感）。"""
    d.ellipse(s(HEAD_CX - HEAD_R, HEAD_CY - HEAD_R + 2, HEAD_CX + HEAD_R, HEAD_CY + HEAD_R + 2),
              fill=SKIN)
    # 下巴阴影
    d.chord(s(64, 82, 96, 98), 0, 180, fill=SKIN_SHADE)


def draw_body(d):
    """女仆裙 + 白围裙 + 金边 + 手臂。"""
    # 脖子
    d.rounded_rectangle(s(74, 86, 86, 104), radius=int(5 * P), fill=SKIN_SHADE)
    # 裙身：上窄下宽的梯形 + 底部圆弧
    d.polygon(s(58, 98, 102, 98, 112, 144, 48, 144), fill=DRESS)
    d.chord(s(44, 124, 116, 158), 0, 180, fill=DRESS)
    # 亮部（肩到胸）
    d.chord(s(58, 92, 102, 130), 180, 360, fill=DRESS_LIGHT)
    # 白围裙
    d.polygon(s(68, 104, 92, 104, 98, 144, 62, 144), fill=APRON)
    d.chord(s(58, 128, 102, 154), 0, 180, fill=APRON)
    # 围裙上缘金边
    d.arc(s(64, 98, 96, 116), 200, 340, fill=TRIM_GOLD, width=int(2.2 * P))
    # 围裙系带
    d.line(s(68, 106, 62, 100), fill=APRON_SHADE, width=int(1.6 * P))
    d.line(s(92, 106, 98, 100), fill=APRON_SHADE, width=int(1.6 * P))
    # 裙摆金边（弧线贴合裙底）
    d.arc(s(44, 124, 116, 158), 20, 160, fill=TRIM_GOLD, width=int(2.4 * P))
    # 手臂：泡泡袖 + 前臂 + 手
    for side in (-1, 1):
        x = HEAD_CX + side * 31
        d.ellipse(s(x - 11, 96, x + 11, 120), fill=DRESS)
        d.ellipse(s(x - 9, 98, x + 9, 112), fill=DRESS_LIGHT)
        d.ellipse(s(x - 8, 112, x + 8, 132), fill=SKIN)
        d.ellipse(s(x - 7, 124, x + 7, 136), fill=SKIN)
        d.arc(s(x - 7, 124, x + 7, 136), 20, 160, fill=SKIN_SHADE, width=int(1.2 * P))


# ============================================================
# 五官
# ============================================================


def draw_eyes(d, mode: str, look: int = 0):
    """眼睛：open / blink / happy / sad / glance / closed。

    元素减法后释放的垂直空间全部投入眼睛：
    - 眼高从 21 增至 25（y 58~84），虹膜 4 层 + 双高光 + 上睫毛加粗
    - 眼距收窄至 ±15，让双眼更聚焦，避免「分太开」的呆滞感
    - 眉毛上移到 y-30 线，与刘海留出呼吸空间
    """
    y = 73
    for side in (-1, 1):
        x = HEAD_CX + side * 15
        if mode in ("blink", "closed"):
            d.arc(s(x - 11, y - 6, x + 11, y + 9), 20, 160, fill=LASH, width=int(3 * P))
            continue
        if mode == "happy":
            d.arc(s(x - 11, y - 8, x + 11, y + 9), 200, 340, fill=LASH, width=int(3 * P))
            continue
        # 眼白（纵向略拉长，更 Q）
        d.ellipse(s(x - 11, y - 12, x + 11, y + 10), fill=EYE_WHITE)
        off = look * 3
        # 虹膜四层：外深 → 中 → 亮 → 瞳孔
        d.ellipse(s(x - 8 + off, y - 11, x + 8 + off, y + 9), fill=IRIS_DARK)
        d.ellipse(s(x - 6 + off, y - 9, x + 6 + off, y + 7), fill=IRIS_MID)
        d.ellipse(s(x - 4 + off, y - 7, x + 4 + off, y + 5), fill=IRIS_LIGHT)
        d.ellipse(s(x - 3 + off, y - 5, x + 2 + off, y + 3), fill=PUPIL)
        # 双高光（主光 + 补光）
        d.ellipse(s(x - 7 + off, y - 10, x - 2 + off, y - 5), fill=(255, 255, 255, 245))
        d.ellipse(s(x + 2 + off, y + 1, x + 5 + off, y + 4), fill=(255, 255, 255, 190))
        # 上睫毛
        d.arc(s(x - 12, y - 14, x + 12, y + 5), 200, 340, fill=LASH, width=int(3 * P))
        # 眉毛（抬高到刘海下沿与眼之间）
        if mode == "sad":
            d.arc(s(x - 10, y - 25, x + 10, y - 13), 20, 160, fill=EYE_BROW, width=int(2 * P))
        else:
            d.arc(s(x - 10, y - 27, x + 10, y - 16), 200, 340, fill=EYE_BROW, width=int(2 * P))


def draw_mouth(d, mode: str):
    y = 84
    cx = HEAD_CX
    if mode == "smile":
        d.arc(s(cx - 7, y - 2, cx + 7, y + 8), 20, 160, fill=INK, width=int(1.8 * P))
    elif mode == "big_smile":
        d.chord(s(cx - 9, y - 4, cx + 9, y + 11), 0, 180, fill=INK)
        d.chord(s(cx - 7, y - 1, cx + 7, y + 8), 0, 180, fill=(232, 108, 128))
        d.chord(s(cx - 4, y + 2, cx + 4, y + 8), 0, 180, fill=(255, 190, 195))
    elif mode == "frown":
        d.arc(s(cx - 7, y + 3, cx + 7, y + 13), 200, 340, fill=INK, width=int(1.8 * P))
    elif mode == "o":
        d.ellipse(s(cx - 4, y - 1, cx + 4, y + 7), fill=(232, 120, 138))
        d.ellipse(s(cx - 4, y - 1, cx + 4, y + 7), outline=INK, width=int(1.4 * P))
    elif mode == "flat":
        d.line(s(cx - 5, y + 5, cx + 5, y + 5), fill=INK, width=int(1.6 * P))


def draw_blush(d):
    for side in (-1, 1):
        x = HEAD_CX + side * 24
        d.ellipse(s(x - 8, 74, x + 8, 85), fill=BLUSH)


# ============================================================
# 状态装饰
# ============================================================


def draw_sparkles(d, phase: int):
    for i, (dx, dy) in enumerate([(-52, -16), (54, -10), (-48, 32), (52, 28)]):
        if (i + phase) % 2 == 0:
            x, y = HEAD_CX + dx, HEAD_CY + dy
            d.line(s(x - 6, y, x + 6, y), fill=(255, 224, 130, 240), width=int(2 * P))
            d.line(s(x, y - 6, x, y + 6), fill=(255, 224, 130, 240), width=int(2 * P))


def draw_thinking(d, phase: int):
    """思考泡泡：沿弧线上升的小圆。"""
    for i in range(3):
        t = i / 2
        x = HEAD_CX + 30 + t * 14
        y = 14 - t * 12 - (3 if (i + phase) % 2 == 0 else 0)
        r = 6 - i * 1.2
        d.ellipse(s(x - r, y - r, x + r, y + r), fill=(255, 255, 255, 235))
        d.ellipse(s(x - r, y - r, x + r, y + r), outline=(150, 168, 200), width=int(1.1 * P))


def draw_sleep(d):
    """offline 的 Z 标记。"""
    x, y = 116, 12
    d.line(s(x, y, x + 12, y, x + 12, y + 12), fill=(112, 112, 132, 228), width=int(2.2 * P))


def draw_hearts(d, phase: int):
    """飘起来的小爱心（摸头 / 撒娇时出现）。"""
    for i, (dx, dy) in enumerate([(-46, -18), (48, -12), (-34, 32), (40, 34)]):
        if (i + phase) % 2 != 0:
            continue
        x, y = HEAD_CX + dx, HEAD_CY + dy - phase * 3
        d.ellipse(s(x - 6, y - 5, x - 1, y + 1), fill=(255, 128, 158, 240))
        d.ellipse(s(x + 1, y - 5, x + 6, y + 1), fill=(255, 128, 158, 240))
        d.polygon(s(x - 6, y - 1, x + 6, y - 1, x, y + 7), fill=(255, 128, 158, 240))


# ============================================================
# 组装
# ============================================================


def make_frame(state: str, idx: int) -> Image.Image:
    bounce, sway, tail, ear = 0, 0, 0, 0
    eyes, mouth, look = "open", "smile", 0

    if state == "pat":
        bounce = [0, -3][idx % 2]
        tail = [0, 4][idx % 2]
        ear = [0, -3][idx % 2]
        eyes, mouth = "blink", "big_smile"
    elif state == "shy":
        bounce = [1, -1, 2][idx % 3]
        tail = [3, -3, 0][idx % 3]
        ear = [2, -2, 0][idx % 3]
        eyes, mouth = "glance", "o"
        look = [-1, 1, 0][idx % 3]
    elif state == "idle":
        bounce = [0, -2, -3, -1][idx % 4]
        sway = [0, 1, 0, -1][idx % 4]
        tail = [0, 2, 3, 1][idx % 4]
        ear = [0, 1, 2, 1][idx % 4]
        eyes = "blink" if idx % 4 == 2 else "open"
    elif state == "up":
        bounce = [0, -5][idx % 2]
        tail = [0, 6][idx % 2]
        ear = [0, -4][idx % 2]
        eyes, mouth = "open", "big_smile"
    elif state == "down":
        bounce = [2, 3][idx % 2]
        tail = [-4, -6][idx % 2]
        ear = [3, 4][idx % 2]
        eyes, mouth = "sad", "frown"
    elif state == "loading":
        tail = [2, -2][idx % 2]
        ear = [2, -2][idx % 2]
        eyes, mouth = "glance", "o"
        look = -1 if idx % 2 == 0 else 1
    else:  # offline
        tail = -3
        ear = 3
        eyes, mouth = "closed", "flat"

    layer = Image.new("RGBA", (LOGICAL_SIZE, LOGICAL_SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")

    # 绘制顺序即图层顺序（后画的在上）：
    #   尾鳍(最底) → 后发 → 身体 → 头 → 耳鳍(穿出发外) → 刘海 → 头巾 → 王冠 → 五官
    draw_tail_fin(d, tail)
    draw_hair_back(d, sway)
    draw_body(d)
    draw_whale_mark(d)
    draw_head(d)
    draw_ear_fins(d, ear)
    draw_bangs(d)
    draw_headband(d)
    draw_crown(d)
    draw_eyes(d, eyes, look)
    draw_mouth(d, mouth)
    if state != "offline":
        draw_blush(d)
    if state == "up":
        draw_sparkles(d, idx)
    if state == "down":
        for side in (-1, 1):
            x = HEAD_CX + side * 16
            d.ellipse(s(x + 6, 74, x + 11, 88), fill=TEAR)
    if state == "loading":
        draw_thinking(d, idx)
    if state == "offline":
        draw_sleep(d)
    if state in ("pat", "shy"):
        draw_blush(d)
        draw_hearts(d, idx)

    # 弹跳位移在超采样画布上完成，保留亚像素精度
    img = Image.new("RGBA", (LOGICAL_SIZE, LOGICAL_SIZE), (0, 0, 0, 0))
    img.alpha_composite(layer, (0, int(bounce * P)))
    img = img.resize((SIZE, SIZE), Image.LANCZOS)
    if state == "offline":
        img = ImageEnhance.Color(img.convert("RGBA")).enhance(0.12)
    return img


def main() -> None:
    """生成全部素材帧与应用图标。

    体积说明：4x 超采样 + LANCZOS 会产生较多中间色，单帧约 28KB（原 2.7KB）。
    曾尝试 FASTOCTREE 量化到 192 色（可减 36%），但眼白/皮肤等平坦区域出现
    可见色带，故放弃量化 —— 448KB 素材在 ~46MB 安装包中占比不足 1%，
    不值得用观感换取这点体积。
    """
    PET_DIR.mkdir(parents=True, exist_ok=True)
    plan = {"idle": 4, "up": 2, "down": 2, "pat": 2, "shy": 3, "loading": 2, "offline": 1}
    for state, count in plan.items():
        for i in range(count):
            make_frame(state, i).save(PET_DIR / f"{state}_{i}.png", optimize=True)
            print("生成", PET_DIR / f"{state}_{i}.png")

    icon = make_frame("idle", 0).resize((256, 256), Image.LANCZOS)
    icon.save(OUT_DIR / "icon.png", optimize=True)
    icon.save(
        OUT_DIR / "icon.ico",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("生成", OUT_DIR / "icon.png", OUT_DIR / "icon.ico")


if __name__ == "__main__":
    main()
