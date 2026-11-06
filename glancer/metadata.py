"""File details for the File info panel.

    ffprobe -show_format -show_streams (JSON) -> duration, codecs, sizes, tags
    Pillow EXIF                               -> camera, date taken, all EXIF tags
    os.stat                                   -> size, created, modified

"basic" is the short list shown first. "raw" is every tag, for the expander.
Location (GPS) data appears only in "raw", never in the basic fields.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image

from glancer.media_types import MediaKind, detect_kind
from glancer.utils import find_binary

FFPROBE_TIMEOUT_S = 10
Pair = tuple[str, str]


@dataclass
class FileInfo:
    basic: list[Pair] = field(default_factory=list)
    raw: list[Pair] = field(default_factory=list)

    def value(self, label: str) -> str | None:
        return next((v for k, v in self.basic if k == label), None)


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            text = f"{size:,} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
            return text if unit == "bytes" else f"{text} ({size:,} bytes)"
        value /= 1024
    return f"{size:,} bytes"


def format_duration(seconds: float) -> str:
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _format_date(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _rate(text: str | None) -> float | None:
    try:
        value = float(Fraction(text)) if text and text != "0/0" else None
    except (ValueError, ZeroDivisionError):
        return None
    return value or None


def probe(path: Path) -> dict[str, Any]:
    ffprobe = find_binary("ffprobe.exe")
    if ffprobe is None:
        return {}
    try:
        result = subprocess.run(
            [str(ffprobe), "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=FFPROBE_TIMEOUT_S, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return json.loads(result.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}


def _rotation(stream: dict[str, Any]) -> int:
    for item in stream.get("side_data_list", []):
        if "rotation" in item:
            return int(float(item["rotation"]))
    return int(float(stream.get("tags", {}).get("rotate", 0) or 0))


def _exif(path: Path) -> tuple[dict[str, Any], dict[str, Any], tuple[int, int] | None]:
    """(EXIF tags by name, GPS tags by name, displayed size after orientation)."""
    try:
        with Image.open(path) as image:
            size = image.size
            exif = image.getexif()
            tags = {ExifTags.TAGS.get(k, str(k)): v for k, v in exif.items()}
            tags.update(
                {ExifTags.TAGS.get(k, str(k)): v
                 for k, v in exif.get_ifd(ExifTags.IFD.Exif).items()}
            )
            gps = {ExifTags.GPSTAGS.get(k, str(k)): v
                   for k, v in exif.get_ifd(ExifTags.IFD.GPSInfo).items()}
            if tags.get("Orientation") in (5, 6, 7, 8):
                size = (size[1], size[0])
            return tags, gps, size
    except Exception:
        return {}, {}, None


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip("\x00 ") or f"<{len(value)} bytes>"
    return str(value)


def read_info(path: Path, kind: MediaKind | None = None) -> FileInfo:
    kind = kind or detect_kind(path)
    info = FileInfo()
    basic, raw = info.basic, info.raw
    st = path.stat()
    basic += [
        ("Name", path.name),
        ("Folder", str(path.parent)),
        ("Size", format_size(st.st_size)),
        ("Created", _format_date(getattr(st, "st_birthtime", st.st_ctime))),
        ("Modified", _format_date(st.st_mtime)),
    ]

    data = probe(path)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    real_streams = [s for s in streams if not s.get("disposition", {}).get("attached_pic")]
    video = next((s for s in real_streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if kind is MediaKind.IMAGE:  # ffprobe calls a JPEG an "image2 sequence"
        basic.append(("Format", f"{path.suffix[1:].upper()} image"))
    elif fmt.get("format_long_name"):
        basic.append(("Format", fmt["format_long_name"]))

    if kind is MediaKind.IMAGE or kind is MediaKind.ANIMATED:
        exif, gps, size = _exif(path)
        if size:
            basic.append(("Dimensions", f"{size[0]} × {size[1]}"))
        camera = " ".join(_text(exif[k]).strip() for k in ("Make", "Model") if exif.get(k))
        if camera:
            basic.append(("Camera", camera))
        taken = exif.get("DateTimeOriginal") or exif.get("DateTime")
        if taken:
            basic.append(("Date taken", _text(taken)))
        if kind is MediaKind.ANIMATED and video and video.get("nb_frames"):
            basic.append(("Frames", str(video["nb_frames"])))
        raw += [(f"EXIF: {k}", _text(v)) for k, v in sorted(exif.items())]
        raw += [(f"GPS: {k}", _text(v)) for k, v in sorted(gps.items())]
    else:
        duration = float(fmt.get("duration") or 0)
        if duration:
            basic.append(("Duration", format_duration(duration)))
        if video:
            width, height = int(video.get("width", 0)), int(video.get("height", 0))
            if _rotation(video) % 180:
                width, height = height, width
            basic.append(("Dimensions", f"{width} × {height}"))
            fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
            if fps:
                basic.append(("Frame rate", f"{fps:.3f}".rstrip("0").rstrip(".") + " fps"))
            basic.append(("Video codec", str(video.get("codec_name", "?")).upper()))
        if audio:
            parts = [str(audio.get("codec_name", "?")).upper()]
            if audio.get("channels"):
                parts.append(f"{audio['channels']} ch")
            if audio.get("sample_rate"):
                parts.append(f"{int(audio['sample_rate']) / 1000:g} kHz")
            basic.append(("Audio", ", ".join(parts)))
        if fmt.get("bit_rate"):
            basic.append(("Bit rate", f"{int(fmt['bit_rate']) / 1000:,.0f} kb/s"))
        tags = {k.lower(): v for k, v in fmt.get("tags", {}).items()}
        for key, label in (("title", "Title"), ("artist", "Artist"), ("album", "Album"),
                           ("date", "Year")):
            if tags.get(key):
                basic.append((label, tags[key]))

    raw += [(f"Format: {k}", str(v)) for k, v in sorted(fmt.get("tags", {}).items())]
    for index, stream in enumerate(streams):
        label = f"Stream {index} ({stream.get('codec_type', '?')})"
        raw += [(f"{label}: {k}", str(v)) for k, v in sorted(stream.get("tags", {}).items())]
    return info
