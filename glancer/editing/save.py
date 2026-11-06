"""Photo crops and the overwrite swap.

Overwrite, after the edited file has been written to a temp file:

    verify temp (ffprobe: duration > 0 and the stream is there; photos: Pillow)
    remember the original's creation time
    original -> Recycle Bin                 (fails? temp is deleted, original untouched)
    temp -> original's name                 (fails? temp is kept as "name (edited).ext")
    restore the creation time; the modified time stays "now"
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageOps, JpegImagePlugin

from glancer.editing.commands import Crop, copy_path
from glancer.file_ops import move_to_recycle_bin
from glancer.media_types import MediaKind
from glancer.metadata import probe

ORIENTATION_TAG = 0x0112
# Formats whose Pillow writers accept EXIF.
EXIF_FORMATS = {"JPEG", "PNG", "WEBP", "TIFF"}


class SaveError(Exception):
    pass


def creation_time(path: Path) -> float:
    st = path.stat()
    return getattr(st, "st_birthtime", st.st_ctime)


def set_creation_time(path: Path, timestamp: float) -> None:
    import pywintypes
    import win32file

    handle = win32file.CreateFile(
        str(path), win32file.GENERIC_WRITE, win32file.FILE_SHARE_READ, None,
        win32file.OPEN_EXISTING, 0, None,
    )
    try:
        win32file.SetFileTime(handle, pywintypes.Time(timestamp), None, None)
    finally:
        handle.Close()


def verify_output(path: Path, kind: MediaKind) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise SaveError("The edited file is empty, so the original was kept.")
    if kind is MediaKind.IMAGE:
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as exc:
            raise SaveError("The edited photo failed a check, so the original was kept.") from exc
        return
    data = probe(path)
    wanted = "video" if kind is MediaKind.VIDEO else "audio"
    has_stream = any(s.get("codec_type") == wanted for s in data.get("streams", []))
    if float(data.get("format", {}).get("duration") or 0) <= 0 or not has_stream:
        raise SaveError("The edited file failed a check, so the original was kept.")


def replace_original(original: Path, temp: Path, kind: MediaKind) -> None:
    """Swap the verified temp file into the original's place (see module doc)."""
    verify_output(temp, kind)
    created = creation_time(original)
    move_to_recycle_bin(original)  # raises: nothing changed yet
    try:
        os.replace(temp, original)
    except OSError as exc:
        kept = copy_path(original)
        try:
            os.replace(temp, kept)
        except OSError:
            kept = temp
        raise SaveError(
            f"The original is in the Recycle Bin, but the edit couldn't take its name. "
            f"It was saved as {kept.name}."
        ) from exc
    set_creation_time(original, created)


def crop_photo(source: Path, crop: Crop, output: Path) -> None:
    """Crop what's on screen (EXIF orientation applied) and keep the other EXIF."""
    with Image.open(source) as image:
        fmt = image.format or "PNG"
        exif = image.getexif()
        sampling = JpegImagePlugin.get_sampling(image) if fmt == "JPEG" else -1
        icc = image.info.get("icc_profile")
        upright = ImageOps.exif_transpose(image) or image
        cropped = upright.crop((crop.x, crop.y, crop.x + crop.w, crop.y + crop.h))
    options: dict[str, object] = {}
    if fmt in EXIF_FORMATS and len(exif):
        if ORIENTATION_TAG in exif:
            exif[ORIENTATION_TAG] = 1  # the pixels are upright now
        options["exif"] = exif.tobytes()
    if icc:
        options["icc_profile"] = icc
    if fmt == "JPEG":
        options["quality"] = 95
        if sampling != -1:
            options["subsampling"] = sampling
    elif fmt == "WEBP":
        options["lossless"] = True
    cropped.save(output, format=fmt, **options)
