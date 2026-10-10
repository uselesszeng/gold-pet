"""快捷方式模块（gold_pet/shortcut.py）测试。

小白用户的核心痛点：桌宠不进任务栏，关掉窗口后找不到入口。
本文件覆盖快捷方式创建 / 删除 / 状态查询，以及非 Windows 平台的空操作。

设计要点：**不往用户真实桌面写文件**。所有创建类测试都重定向到 tmp_path，
避免测试污染用户环境（该坑在本项目 v0.5.0 的 alert_history.json 上已经踩过一次）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gold_pet import shortcut  # noqa: E402

# --------------------------------------------------------------------------- 基础


def test_platform_support_is_bool():
    """is_supported 必须在任何平台都返回布尔值，不能抛异常。"""
    assert isinstance(shortcut.is_supported(), bool)


def test_shortcut_path_returns_none_without_folder(monkeypatch):
    """取不到系统文件夹时应返回 None，而不是拼出错误路径。"""
    monkeypatch.setattr(shortcut, "desktop_dir", lambda: None)
    assert shortcut.shortcut_path("desktop") is None
    monkeypatch.setattr(shortcut, "start_menu_dir", lambda: None)
    assert shortcut.shortcut_path("startmenu") is None


def test_is_created_false_when_no_folder(monkeypatch):
    """目录不存在时 is_created 应为 False，且不抛异常。"""
    monkeypatch.setattr(shortcut, "desktop_dir", lambda: None)
    assert shortcut.is_created("desktop") is False


def test_target_path_points_to_existing_file():
    """快捷方式目标必须是一个真实存在的文件，否则快捷方式是坏的。"""
    target = shortcut._target_path()
    if target is None:
        pytest.skip("当前环境无解释器路径")
    assert target.exists(), f"目标不存在：{target}"


def test_target_args_empty_when_frozen(monkeypatch):
    """打包后不应给 exe 传参数。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert shortcut._target_args() == ""


# --------------------------------------------------------------------------- 状态


def test_status_has_both_locations():
    """status() 必须同时报告桌面与开始菜单两个位置。"""
    st = shortcut.status()
    assert set(st.keys()) == {"desktop", "startmenu"}
    assert all(isinstance(v, bool) for v in st.values())


# --------------------------------------------------------------------------- 创建 / 删除（重定向到 tmp_path）


@pytest.fixture
def fake_dirs(tmp_path, monkeypatch):
    """把桌面/开始菜单重定向到临时目录，避免污染用户真实环境。"""
    desktop = tmp_path / "Desktop"
    startmenu = tmp_path / "StartMenu"
    desktop.mkdir()
    startmenu.mkdir()
    monkeypatch.setattr(shortcut, "desktop_dir", lambda: desktop)
    monkeypatch.setattr(shortcut, "start_menu_dir", lambda: startmenu)
    return desktop, startmenu


def test_create_and_remove_roundtrip(fake_dirs):
    """创建 → 存在 → 删除 → 不存在，完整往返。

    仅在支持创建的平台（Windows）上验证真实 .lnk；
    非 Windows 下 create 应安全返回 False。
    """
    desktop, _ = fake_dirs
    lnk = desktop / shortcut.LINK_NAME

    ok = shortcut.create("desktop")
    if not shortcut.is_supported():
        assert ok is False, "非 Windows 平台不应报告创建成功"
        assert not lnk.exists()
        return

    assert ok is True, "创建快捷方式失败"
    assert lnk.exists(), "快捷方式文件未生成"
    assert lnk.stat().st_size > 0, "快捷方式文件是空的"
    assert shortcut.is_created("desktop") is True

    assert shortcut.remove("desktop") is True
    assert not lnk.exists()
    assert shortcut.is_created("desktop") is False


def test_create_is_idempotent(fake_dirs):
    """重复创建应覆盖而不是报错（方便用户删掉后重新生成）。"""
    desktop, _ = fake_dirs
    if not shortcut.is_supported():
        pytest.skip("当前平台不支持创建快捷方式")
    assert shortcut.create("desktop") is True
    first_size = (desktop / shortcut.LINK_NAME).stat().st_size
    assert shortcut.create("desktop") is True
    assert (desktop / shortcut.LINK_NAME).stat().st_size == first_size


def test_create_many_returns_per_location_result(fake_dirs):
    """批量创建要逐个返回结果，便于 UI 精确提示成功/失败。"""
    desktop, startmenu = fake_dirs
    results = shortcut.create_many(["desktop", "startmenu"])
    assert set(results.keys()) == {"desktop", "startmenu"}
    assert all(isinstance(v, bool) for v in results.values())
    if shortcut.is_supported():
        assert all(results.values()), f"创建失败：{results}"
        assert (desktop / shortcut.LINK_NAME).exists()
        assert (startmenu / shortcut.LINK_NAME).exists()
        shortcut.remove("desktop")
        shortcut.remove("startmenu")


def test_remove_missing_file_returns_false(fake_dirs):
    """删除不存在的快捷方式应返回 False，不抛异常。"""
    assert shortcut.remove("desktop") is False


def test_create_without_support_returns_false(monkeypatch, fake_dirs):
    """不支持创建时 create 必须安全返回 False。"""
    monkeypatch.setattr(shortcut, "is_supported", lambda: False)
    assert shortcut.create("desktop") is False


def test_create_when_target_missing_returns_false(monkeypatch):
    """目标路径取不到时不应崩，直接返回 False。"""
    monkeypatch.setattr(shortcut, "is_supported", lambda: True)
    monkeypatch.setattr(shortcut, "_target_path", lambda: None)
    assert shortcut.create("desktop") is False


# ----------------------------------------------------------- lnk 内容正确性


def test_created_lnk_points_to_target(fake_dirs):
    """关键验证：.lnk 必须真的指向目标程序。

    只断言「文件存在」是不够的——一个指向空路径的 .lnk 同样存在，
    但双击毫无反应。这里用 IShellLink::GetPath 反向读取确认。
    """
    if not shortcut.is_supported():
        pytest.skip("当前平台不支持创建快捷方式")

    import ctypes
    from ctypes import wintypes

    desktop, _ = fake_dirs
    assert shortcut.create("desktop") is True
    lnk = desktop / shortcut.LINK_NAME

    ole32 = ctypes.windll.ole32
    co_init = ole32.CoInitialize(None)
    try:
        clsid = shortcut._guid_from_string(shortcut._CLSID_ShellLink)
        iid = shortcut._guid_from_string(shortcut._IID_IShellLinkW)
        ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(ptr)
        )
        assert hr == 0, f"CoCreateInstance 失败 hr=0x{hr & 0xFFFFFFFF:08x}"

        vt = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        qi = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(shortcut._GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )(vt[0])
        iid_pf = shortcut._guid_from_string(shortcut._IID_IPersistFile)
        pf = ctypes.c_void_p()
        assert qi(ptr, ctypes.byref(iid_pf), ctypes.byref(pf)) == 0

        pvt = ctypes.cast(pf, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        load = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_int
        )(pvt[5])
        assert load(pf, str(lnk), 0) == 0, "加载 .lnk 失败"

        buf = ctypes.create_unicode_buffer(1024)
        getpath = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_uint,
        )(vt[3])
        getpath(ptr, buf, 1024, None, 0)

        expected = shortcut._target_path()
        assert expected is not None
        assert Path(buf.value).resolve() == expected, (
            f".lnk 指向 {buf.value}，应为 {expected}"
        )

        # 描述也要写上，鼠标悬停时才不是空白
        dbuf = ctypes.create_unicode_buffer(1024)
        getdesc = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int
        )(vt[6])
        getdesc(ptr, dbuf, 1024)
        assert dbuf.value == shortcut.DESCRIPTION
    finally:
        if co_init == 0:
            ole32.CoUninitialize()


def test_created_lnk_has_icon(fake_dirs, tmp_path):
    """指定图标时应写入图标路径（否则桌面显示白板图标）。"""
    if not shortcut.is_supported():
        pytest.skip("当前平台不支持创建快捷方式")

    import ctypes
    from ctypes import wintypes

    icon = tmp_path / "fake.ico"
    icon.write_bytes(b"\x00\x00\x01\x00")  # 假图标文件，只验证路径被写入

    desktop, _ = fake_dirs
    assert shortcut.create("desktop", icon=icon) is True

    ole32 = ctypes.windll.ole32
    co_init = ole32.CoInitialize(None)
    try:
        clsid = shortcut._guid_from_string(shortcut._CLSID_ShellLink)
        iid = shortcut._guid_from_string(shortcut._IID_IShellLinkW)
        ptr = ctypes.c_void_p()
        ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(ptr)
        )
        vt = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        qi = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(shortcut._GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )(vt[0])
        iid_pf = shortcut._guid_from_string(shortcut._IID_IPersistFile)
        pf = ctypes.c_void_p()
        qi(ptr, ctypes.byref(iid_pf), ctypes.byref(pf))
        pvt = ctypes.cast(pf, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        load = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_int
        )(pvt[5])
        load(pf, str(desktop / shortcut.LINK_NAME), 0)

        ibuf = ctypes.create_unicode_buffer(1024)
        iidx = ctypes.c_int()
        geticon = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        )(vt[16])
        geticon(ptr, ibuf, 1024, ctypes.byref(iidx))
        assert ibuf.value, "图标路径未写入"
        assert Path(ibuf.value).name == "fake.ico"
    finally:
        if co_init == 0:
            ole32.CoUninitialize()
