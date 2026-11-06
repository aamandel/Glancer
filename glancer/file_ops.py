"""Small file operations: Show in Explorer, Recycle Bin, safe new names.

Deleting only ever goes to the Recycle Bin. Windows silently deletes files
*permanently* on drives without a Recycle Bin (network shares, most USB
sticks) when asked without confirmation, so those drives are refused instead.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

from send2trash import send2trash

DRIVE_FIXED = 3
RETRIES = 10
RETRY_DELAY_S = 0.1


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_int64),
                ("i64NumItems", ctypes.c_int64)]


_shell32 = ctypes.WinDLL("shell32")
_SHQueryRecycleBinW = _shell32.SHQueryRecycleBinW
_SHQueryRecycleBinW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_SHQUERYRBINFO)]
_SHQueryRecycleBinW.restype = ctypes.c_long
_GetDriveTypeW = ctypes.WinDLL("kernel32").GetDriveTypeW
_GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
_GetDriveTypeW.restype = wintypes.UINT


class RecycleError(OSError):
    pass


def show_in_explorer(path: Path) -> None:
    """Open an Explorer window with the file selected."""
    # explorer.exe parses its own command line, so pass one pre-quoted string.
    subprocess.Popen(f'explorer.exe /select,"{path}"')


def can_recycle(path: Path) -> bool:
    """True only for local fixed drives that have a working Recycle Bin."""
    # abspath, not resolve(): resolving can block on an unreachable network share.
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    if root.startswith("\\\\") or _GetDriveTypeW(root) != DRIVE_FIXED:
        return False
    info = _SHQUERYRBINFO(ctypes.sizeof(_SHQUERYRBINFO), 0, 0)
    return _SHQueryRecycleBinW(root, ctypes.byref(info)) == 0


def send_to_trash(path: Path) -> None:
    """The actual Recycle Bin call (separate so tests can swap it out)."""
    send2trash(str(path))


def move_to_recycle_bin(path: Path) -> None:
    if not can_recycle(path):
        raise RecycleError(
            f"{path.name} is on a drive without a Recycle Bin, so Glancer won't delete it."
        )
    # mpv may need a moment to let go of the file after unloading it.
    for attempt in range(RETRIES):
        try:
            send_to_trash(path)
            break
        except OSError:
            if attempt == RETRIES - 1:
                raise
            time.sleep(RETRY_DELAY_S)
    if path.exists():
        raise RecycleError(f"{path.name} could not be moved to the Recycle Bin.")


def unique_path(path: Path) -> Path:
    """path, or "stem (2).ext", "stem (3).ext", ... if it already exists."""
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def frame_filename(video: Path, frame_number: int) -> Path:
    return unique_path(video.with_name(f"{video.stem}_frame_{frame_number:06d}.png"))
