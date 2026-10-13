"""桌面 / 开始菜单快捷方式（Windows）。

小白用户下载 exe 后双击运行，程序**不在任务栏显示**（桌宠用 Qt.Tool 是设计使然），
关掉窗口又想再打开时无处可找。本模块负责把「鲸娘的入口」放到用户找得到的地方。

实现方式：直接用 ctypes 调 Win32 COM（IShellLink + IPersistFile），
**零新增依赖**——不引入 pywin32 / winshell 这类第三方包。

非 Windows 平台下所有函数都是安全空操作，与 autostart.py 保持同一模式。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from pathlib import Path

APP_NAME = "黄金桌宠"
LINK_NAME = "黄金桌宠.lnk"
# 快捷方式描述（鼠标悬停时显示）
DESCRIPTION = "桌面实时金价挂件 · 左键摸摸头 / 右键菜单"

_log = logging.getLogger("gold_pet")

# ---------------------------------------------------------------------------
# Win32 / COM 常量
# ---------------------------------------------------------------------------
_CLSCTX_INPROC_SERVER = 1

# CLSID_ShellLink：{00021401-0000-0000-C000-000000000046}
# 注意：这里是与 IShellLink **同名但不同用途**的 GUID —— CoCreateInstance 的
# 第一个参数是「组件类 ID」，第二个才是「接口 ID」。两者同为这个值时不会报错，
# 但若只传一个（把接口 IID 当 CLSID）会返回 E_NOINTERFACE(0x80004002)。
_CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
# IID_IShellLinkW：{000214F9-0000-0000-C000-000000000046}
_IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
# IPersistFile 的 IID：{0000010b-0000-0000-C000-000000000046}
_IID_IPersistFile = "{0000010b-0000-0000-C000-000000000046}"

# IShellLink::SetPath 的 SLGP 标志
_SLGP_UNCPRIORITY = 0x0002

# CSIDL / KNOWNFOLDERID 对应的 shell 特殊文件夹名
_CSIDL_DESKTOPDIRECTORY = 0x0010
_CSIDL_PROGRAMS = 0x0002
_CSIDL_COMMON_PROGRAMS = 0x0017


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid_from_string(text: str) -> _GUID:
    """把 '{00021401-...}' 形式的 GUID 字符串转成 _GUID 结构。"""
    raw = text.strip("{}").replace("-", "")
    return _GUID(
        int(raw[0:8], 16),
        int(raw[8:12], 16),
        int(raw[12:16], 16),
        (ctypes.c_ubyte * 8)(*bytes.fromhex(raw[16:32])),
    )


def _target_path() -> Path | None:
    """快捷方式要指向的目标。

    打包后指向 exe 自身；源码运行时指向 pythonw.exe（开发调试用）。
    返回 None 表示环境不支持（非 Windows 且无解释器路径）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return (pythonw if pythonw.exists() else exe).resolve()


def _target_args() -> str:
    """源码运行时需要把 run.py 作为参数传进去；打包后无参数。"""
    if getattr(sys, "frozen", False):
        return ""
    run_py = Path(__file__).resolve().parent.parent / "run.py"
    return f'"{run_py}"' if run_py.exists() else ""


def _shell_folder(csidl: int) -> Path | None:
    """取 Windows 特殊文件夹路径（桌面 / 开始菜单程序组）。"""
    if sys.platform != "win32":
        return None
    try:
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        # SHGetFolderPathW(hwndOwner, nFolder, hToken, dwFlags, pszPath)
        ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
        return Path(buf.value) if buf.value else None
    except Exception as exc:  # pragma: no cover - 仅真实 Windows 环境触发
        _log.warning("取系统文件夹失败（csidl=0x%04x）：%s", csidl, exc)
        return None


def desktop_dir() -> Path | None:
    """桌面目录。"""
    return _shell_folder(_CSIDL_DESKTOPDIRECTORY)


def start_menu_dir() -> Path | None:
    """开始菜单 → 所有程序（当前用户）。"""
    return _shell_folder(_CSIDL_PROGRAMS)


def shortcut_path(where: str) -> Path | None:
    """快捷方式应落到的完整路径。where ∈ {'desktop', 'startmenu'}。"""
    folder = desktop_dir() if where == "desktop" else start_menu_dir()
    return (folder / LINK_NAME) if folder else None


def is_supported() -> bool:
    """当前平台是否支持创建快捷方式。"""
    if sys.platform != "win32":
        return False
    return _target_path() is not None


def is_created(where: str) -> bool:
    """指定位置是否已存在鲸娘的快捷方式。"""
    path = shortcut_path(where)
    return bool(path and path.exists())


# ---------------------------------------------------------------------------
# 核心：创建 .lnk
# ---------------------------------------------------------------------------
def _create_lnk(lnk_path: Path, target: Path, args: str, icon: Path | None) -> bool:
    """用 IShellLink + IPersistFile 创建快捷方式。成功返回 True。"""
    ole32 = ctypes.windll.ole32

    # 本线程若尚未初始化 COM，需要先初始化（幂等，失败不阻塞）
    co_init = ole32.CoInitialize(None)

    shell_link = ctypes.c_void_p()
    persist = ctypes.c_void_p()
    try:
        clsid_link = _guid_from_string(_CLSID_ShellLink)
        iid_link = _guid_from_string(_IID_IShellLinkW)
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid_link),
            None,
            _CLSCTX_INPROC_SERVER,
            ctypes.byref(iid_link),
            ctypes.byref(shell_link),
        )
        if hr != 0 or not shell_link:
            _log.warning("CoCreateInstance(IShellLink) 失败，hr=0x%08x", hr)
            return False

        # 通过 vtable 调用 IShellLink 方法
        # vtable 顺序：QueryInterface, AddRef, Release, GetPath, GetIDList,
        #              SetIDList, GetDescription, SetDescription, GetWorkingDirectory,
        #              SetWorkingDirectory, GetArguments, SetArguments, GetHotkey,
        #              SetHotkey, GetShowCmd, SetShowCmd, GetIconLocation,
        #              SetIconLocation, SetRelativePath, Resolve, SetPath
        vtable = ctypes.cast(shell_link, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

        def _call(index: int, *argtypes):
            proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
            return proto(vtable[index])

        # index 3 = GetPath, 4 = GetIDList ... 逐个对应，见上方顺序注释
        set_path = _call(20, wintypes.LPCWSTR, ctypes.c_uint)
        set_description = _call(7, wintypes.LPCWSTR)
        set_working_dir = _call(9, wintypes.LPCWSTR)
        set_args = _call(11, wintypes.LPCWSTR)
        set_icon = _call(17, wintypes.LPCWSTR, ctypes.c_int)
        set_show = _call(15, ctypes.c_int)

        set_path(shell_link, str(target), 0)
        set_description(shell_link, DESCRIPTION)
        set_working_dir(shell_link, str(target.parent))
        if args:
            set_args(shell_link, args)
        set_show(shell_link, 1)  # SW_SHOWNORMAL
        if icon is not None and icon.exists():
            set_icon(shell_link, str(icon), 0)

        # QueryInterface 拿 IPersistFile（vtable index 0）
        qi = _call(0, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))
        iid_pf = _guid_from_string(_IID_IPersistFile)
        hr = qi(shell_link, ctypes.byref(iid_pf), ctypes.byref(persist))
        if hr != 0 or not persist:
            _log.warning("QueryInterface(IPersistFile) 失败，hr=0x%08x", hr)
            return False

        # IPersistFile::Save (vtable index 6)
        pf_vtable = ctypes.cast(persist, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        save_proto = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_int
        )
        save = save_proto(pf_vtable[6])

        lnk_path.parent.mkdir(parents=True, exist_ok=True)
        hr = save(persist, str(lnk_path), 1)  # TRUE = 记住路径
        if hr != 0:
            _log.warning("IPersistFile::Save 失败，hr=0x%08x", hr)
            return False
        return lnk_path.exists()
    except Exception as exc:
        _log.warning("创建快捷方式异常：%s", exc)
        return False
    finally:
        # 释放 COM 引用
        for ptr in (persist, shell_link):
            if ptr:
                try:
                    release_vtable = ctypes.cast(
                        ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
                    ).contents
                    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(release_vtable[2])
                    release(ptr)
                except Exception:
                    pass
        if co_init == 0:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass


def create(where: str, icon: Path | None = None) -> bool:
    """在指定位置创建快捷方式。where ∈ {'desktop', 'startmenu'}。

    重复创建会**覆盖**（幂等），便于用户手动删掉后重新生成。
    """
    if not is_supported():
        _log.info("当前平台不支持创建快捷方式（where=%s）", where)
        return False
    target = _target_path()
    lnk = shortcut_path(where)
    if target is None or lnk is None:
        return False
    ok = _create_lnk(lnk, target, _target_args(), icon)
    if ok:
        _log.info("已创建快捷方式：%s → %s", lnk, target)
    return ok


def remove(where: str) -> bool:
    """删除指定位置的快捷方式。"""
    path = shortcut_path(where)
    if path and path.exists():
        try:
            path.unlink()
            _log.info("已删除快捷方式：%s", path)
            return True
        except OSError as exc:
            _log.warning("删除快捷方式失败：%s", exc)
    return False


def create_many(wheres: list[str], icon: Path | None = None) -> dict[str, bool]:
    """批量创建，返回 {位置: 是否成功}。"""
    return {w: create(w, icon=icon) for w in wheres}


def status() -> dict[str, bool]:
    """各位置当前的创建状态，供设置面板回显。"""
    return {w: is_created(w) for w in ("desktop", "startmenu")}
