"""素材完整性测试。

这些用例守护 `ui.py::_load_frames()` 对素材的**隐式契约**：
- 文件名必须是 ``{state}_{index}.png``
- 每个状态的帧数必须与 ``ui.STATES`` 的计数一致
- 尺寸必须是 160x160（与 ``ui.py`` 的 ``setFixedSize(160, 160)`` 一致）

没有这层保护时，改 `gen_assets.py` 很容易「少生成一帧」或「改了尺寸」，
表现为桌宠某个状态静止不动或头像被裁切，且不会报错——属于静默失效。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from gold_pet.ui import STATES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PET_DIR = ROOT / "assets" / "pet"
ICON_PNG = ROOT / "assets" / "icon.png"
ICON_ICO = ROOT / "assets" / "icon.ico"

PET_SIZE = 160


def test_pet_dir_exists():
    assert PET_DIR.is_dir(), f"缺少素材目录：{PET_DIR}"


@pytest.mark.parametrize(("state", "frames", "interval"), sorted(
    (k, v[0], v[1]) for k, v in STATES.items()
))
def test_every_state_has_all_frames(state, frames, interval):
    """每个状态的实际帧数必须等于 STATES 声明的帧数。"""
    missing = [i for i in range(frames) if not (PET_DIR / f"{state}_{i}.png").is_file()]
    assert not missing, f"{state} 缺少帧：{missing}（STATES 声明 {frames} 帧）"


def test_no_orphan_frames():
    """反向检查：pet 目录里不应有多余文件（改名残留会让旧帧永远不被加载）。"""
    expected = {f"{state}_{i}.png" for state, (n, _) in STATES.items() for i in range(n)}
    actual = {p.name for p in PET_DIR.glob("*.png")}
    assert actual == expected, f"多余帧：{actual - expected}；缺失帧：{expected - actual}"


def test_all_frames_same_size():
    """全部帧必须同为 PET_SIZE，否则桌宠在不同状态下会跳动。"""
    bad = {}
    for path in sorted(PET_DIR.glob("*.png")):
        size = Image.open(path).size
        if size != (PET_SIZE, PET_SIZE):
            bad[path.name] = size
    assert not bad, f"尺寸不符（应为 {PET_SIZE}x{PET_SIZE}）：{bad}"


def test_frames_are_transparent_png():
    """帧必须是带透明通道的 PNG，否则桌宠会显示成方块。"""
    for path in sorted(PET_DIR.glob("*.png")):
        img = Image.open(path)
        assert img.mode == "RGBA", f"{path.name} 模式为 {img.mode}，应为 RGBA"
        # 四角必须完全透明（内容居中，不应铺满画布）
        for corner in ((0, 0), (PET_SIZE - 1, 0), (0, PET_SIZE - 1)):
            assert img.getpixel(corner)[3] == 0, f"{path.name} 角落 {corner} 不透明"


def test_frames_have_visible_content():
    """每帧都应有实际绘制内容，且不能溢出画布。"""
    for path in sorted(PET_DIR.glob("*.png")):
        bbox = Image.open(path).convert("RGBA").getbbox()
        assert bbox is not None, f"{path.name} 是空帧"
        left, top, right, bottom = bbox
        assert right - left >= 40, f"{path.name} 内容过窄：{bbox}"
        assert bottom - top >= 40, f"{path.name} 内容过矮：{bbox}"
        assert 0 <= left and right <= PET_SIZE, f"{path.name} 内容横向溢出：{bbox}"
        assert 0 <= top and bottom <= PET_SIZE, f"{path.name} 内容纵向溢出：{bbox}"


def test_icon_files_present_and_square():
    assert ICON_PNG.is_file(), "缺少 assets/icon.png"
    assert ICON_ICO.is_file(), "缺少 assets/icon.ico"
    assert Image.open(ICON_PNG).size == (256, 256), "icon.png 应为 256x256"
    # .ico 需含多尺寸，Windows 任务栏/资源管理器才会清晰
    ico = Image.open(ICON_ICO)
    assert ico.format == "ICO"
