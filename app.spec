# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir 打包配置（英文文件名，避免 workflow 引号问题）。

重要：
1. 使用 --onedir，不使用 --onefile。
2. ffmpeg.exe 与 ffprobe.exe 必须放在本文件所在目录（仓库根目录）。
3. EXE 的 contents_directory='.' 让 onedir 采用平铺布局，保证 ffmpeg.exe /
   ffprobe.exe 与“字幕烧录工具.exe”位于同一目录，符合程序运行期定位规则。
4. 最终可执行文件名为“字幕烧录工具.exe”，但 spec 文件名保持 app.spec。
"""

from pathlib import Path


for _name in ("ffmpeg.exe", "ffprobe.exe"):
    if not Path(_name).is_file():
        raise SystemExit(
            f"[app.spec] 缺少 {_name}。请先把 static 版本的 ffmpeg.exe 和 "
            "ffprobe.exe 放到仓库根目录（与 app.spec 同一级）。"
        )

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[
        ("ffmpeg.exe", "."),
        ("ffprobe.exe", "."),
    ],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="字幕烧录工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="字幕烧录工具",
)
