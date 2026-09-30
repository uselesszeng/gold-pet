# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（gold-pet.spec）

用法：
  Windows: pyinstaller --noconfirm --clean gold-pet.spec   （或双击 build.bat）
  产出：   dist/黄金桌宠.exe
"""

import sys
from pathlib import Path

# 图标仅 Windows / macOS 支持
icon = "assets/icon.ico" if sys.platform in ("win32", "darwin") else None

# ---------------------------------------------------------------------------
# 版本资源（Windows）
# 把 gold_pet/__init__.py 的 __version__ 写进 exe 的版本信息里，
# 让「文件属性 → 详细信息」能直接看到版本。此前 spec 不带版本，
# 导致产物无法自证版本，曾出现「文件名叫 v0.5.2、跑起来日志写 0.5.1」
# 的错版发布（见 程序修改点.md v0.5.3）。
# ---------------------------------------------------------------------------
version_file: str | None = None
if sys.platform == "win32":
    _ns: dict[str, object] = {}
    _init = Path("gold_pet/__init__.py")
    exec(_init.read_text(encoding="utf-8"), _ns)  # noqa: S102
    _ver = str(_ns["__version__"])
    _parts = (_ver.split(".") + ["0", "0", "0"])[:4]
    _tuple = ", ".join(str(int(p)) for p in _parts)
    version_file = "version_info.txt"
    Path(version_file).write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({_tuple}),
    prodvers=({_tuple}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', 'TJCoding'),
        StringStruct('FileDescription', '黄金桌宠 Gold Pet'),
        StringStruct('FileVersion', '{_ver}'),
        StringStruct('InternalName', 'gold-pet'),
        StringStruct('LegalCopyright', 'MIT License'),
        StringStruct('OriginalFilename', '黄金桌宠.exe'),
        StringStruct('ProductName', '黄金桌宠'),
        StringStruct('ProductVersion', '{_ver}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""",
        encoding="utf-8",
    )

excludes = [
    "tkinter",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtNetworkAuth",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSerialPort",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtPdf",
    "PySide6.QtSvgWidgets",
]

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=[("assets", "assets")],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="黄金桌宠",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
    version=version_file,
)
