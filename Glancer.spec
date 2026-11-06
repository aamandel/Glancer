# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project_dir = Path(SPECPATH)
# Large third-party binaries live in bin/ (not in git). Phase 7 finalizes
# packaging (ffmpeg/ffprobe for editing, installer, icon).
binaries = []
for name in ("libmpv-2.dll",):
    path = project_dir / "bin" / name
    if path.is_file():
        binaries.append((str(path), "."))

a = Analysis(
    ["run_glancer.py"],
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
    name="Glancer",
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
