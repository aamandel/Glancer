# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project_dir = Path(SPECPATH)
mpv_path = project_dir / "bin" / "mpv.exe"
d3dcompiler_path = project_dir / "bin" / "d3dcompiler_43.dll"
binaries = []
if mpv_path.is_file():
    binaries.append((str(mpv_path), "."))
if d3dcompiler_path.is_file():
    binaries.append((str(d3dcompiler_path), "."))

a = Analysis(
    ["run_framestepper.py"],
    pathex=[str(project_dir)],
    binaries=binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FrameStepper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
