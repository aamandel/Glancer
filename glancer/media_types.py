"""Which files Glancer opens, and how each kind behaves.

Files are accepted strictly by extension. mpv will happily open many other
things (it even shows the thumbnail inside a .3mf 3D-print archive), so the
extension check must happen before anything is handed to mpv.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path


class MediaKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    ANIMATED = "animated"  # animated GIF/WebP: behaves like a looping video

    @property
    def has_timeline(self) -> bool:
        return self is not MediaKind.IMAGE

    @property
    def has_frames(self) -> bool:
        """Whether Left/Right step frames."""
        return self in (MediaKind.VIDEO, MediaKind.ANIMATED)


VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv"})
AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
)
IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
)
# Image formats that may contain more than one frame.
POSSIBLY_ANIMATED = frozenset({".gif", ".webp"})

SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def kind_from_extension(path: Path) -> MediaKind | None:
    """Kind based on the extension alone (GIF/WebP report IMAGE)."""
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return MediaKind.VIDEO
    if suffix in AUDIO_EXTENSIONS:
        return MediaKind.AUDIO
    if suffix in IMAGE_EXTENSIONS:
        return MediaKind.IMAGE
    return None


def is_animated_image(path: Path) -> bool:
    """True for GIF/WebP files with more than one frame."""
    if path.suffix.lower() not in POSSIBLY_ANIMATED:
        return False
    try:
        from PIL import Image

        with Image.open(path) as image:
            return getattr(image, "n_frames", 1) > 1
    except Exception:
        return False


def detect_kind(path: Path) -> MediaKind | None:
    """Full detection, including whether a GIF/WebP is animated."""
    kind = kind_from_extension(path)
    if kind is MediaKind.IMAGE and is_animated_image(path):
        return MediaKind.ANIMATED
    return kind


def _patterns(extensions: frozenset[str]) -> str:
    return " ".join(f"*{ext}" for ext in sorted(extensions))


def open_dialog_filter() -> str:
    """Qt-style filter string for the Open dialog."""
    return ";;".join(
        (
            f"All media ({_patterns(SUPPORTED_EXTENSIONS)})",
            f"Videos ({_patterns(VIDEO_EXTENSIONS)})",
            f"Photos ({_patterns(IMAGE_EXTENSIONS)})",
            f"Audio ({_patterns(AUDIO_EXTENSIONS)})",
        )
    )
