from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def format_time(seconds: float | int | None) -> str:
    """Format seconds as H:MM:SS.mmm or MM:SS.mmm."""
    if seconds is None:
        seconds = 0
    total_ms = max(0, round(float(seconds) * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", application_dir()))


def binary_dirs() -> list[Path]:
    """Folders that may hold libmpv-2.dll, ffmpeg.exe and ffprobe.exe."""
    candidates = [
        bundled_dir(),
        bundled_dir() / "bin",
        application_dir(),
        application_dir() / "bin",
    ]
    unique: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir() and candidate not in unique:
            unique.append(candidate)
    return unique


def find_binary(filename: str) -> Path | None:
    """Find a bundled binary, falling back to PATH."""
    for folder in binary_dirs():
        candidate = folder / filename
        if candidate.is_file():
            return candidate
    from_path = shutil.which(filename)
    return Path(from_path) if from_path else None


def first_file_argument(arguments: list[str]) -> Path | None:
    """Return the first existing file passed to the application."""
    for argument in arguments:
        candidate = Path(os.path.abspath(argument))
        if candidate.is_file():
            return candidate
    return None
