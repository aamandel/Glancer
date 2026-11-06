# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build: a one-folder app in dist\\Glancer\\ (faster startup than one-file).

    dist\\Glancer\\
        Glancer.exe
        _internal\\            Python, Qt, ... (PyInstaller's support files)
            assets\\glancer.ico
            bin\\              libmpv-2.dll, ffmpeg.exe, ffprobe.exe + their DLLs

bin\\ is copied in after PyInstaller finishes (end of this file). Given to
Analysis, PyInstaller would analyse those DLLs, copy ffmpeg's to the top of
_internal\\ and merge same-named files. glancer.utils.binary_dirs finds
_internal\\bin.
"""
from pathlib import Path

project_dir = Path(SPECPATH)
bin_dir = project_dir / "bin"

# Only what Glancer uses: libmpv, the two ffmpeg tools and ffmpeg's shared DLLs.
bundled = ["libmpv-2.dll", "ffmpeg.exe", "ffprobe.exe"]
bundled += [p.name for p in bin_dir.glob("av*.dll")] + [p.name for p in bin_dir.glob("sw*.dll")]
missing = [name for name in ("libmpv-2.dll", "ffmpeg.exe", "ffprobe.exe") if not (bin_dir / name).is_file()]
if missing:
    raise SystemExit(f"bin\\ is missing {', '.join(missing)} (see README)")
datas = [(str(project_dir / "assets" / "glancer.ico"), "assets")]

a = Analysis(
    ["run_glancer.py"],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # Qt modules Glancer doesn't use (keeps the folder smaller).
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf",
              "tkinter", "numpy", "charset_normalizer"],
    noarchive=False,
)
# Qt's software OpenGL fallback is useless here: mpv needs the GPU's OpenGL.
a.binaries = [b for b in a.binaries if not b[0].lower().endswith("opengl32sw.dll")]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Glancer",
    icon=str(project_dir / "assets" / "glancer.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Glancer",
)

import shutil

target = Path(DISTPATH) / "Glancer" / "_internal" / "bin"
target.mkdir(parents=True, exist_ok=True)
for name in bundled + ["ffmpeg-LICENSE.txt"]:
    if (bin_dir / name).is_file():
        shutil.copy2(bin_dir / name, target / name)
