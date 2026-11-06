"""ffmpeg command lines for trims and crops (pure functions, unit-tested).

    Precise           re-encode: H.264 CRF 18 + AAC 192k, frame-exact cuts
    Fast Lossless     stream copy: no quality loss, cut snaps to a keyframe
    Precise Lossless  FFV1 + FLAC in .mkv: frame-exact, no quality loss, large

Trim times come from mpv's time-pos for the chosen frames. The end time is
exclusive: the caller passes the last kept frame's time plus half a frame,
so that frame is kept and the next one is not.

    ffmpeg [-ss start] -i src [-t end-start] -map … [-vf crop=w:h:x:y] codecs out

Re-encoding applies the source's rotation (ffmpeg auto-rotates), so crop
coordinates are in the upright picture, the same as on screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from glancer.media_types import MediaKind
from glancer.settings import EditMode

LOSSLESS_AUDIO_CODECS = {"flac", "alac"} | {f"pcm_{x}" for x in (
    "s16le", "s24le", "s32le", "f32le", "f64le", "u8", "s16be", "s24be", "s32be"
)}
# Precise audio re-encodes with the source's codec at high quality.
AUDIO_ENCODERS = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "0"],
    "aac": ["-c:a", "aac", "-b:a", "256k"],
    "vorbis": ["-c:a", "libvorbis", "-q:a", "8"],
    "opus": ["-c:a", "libopus", "-b:a", "192k"],
    "wmav2": ["-c:a", "wmav2", "-b:a", "256k"],
}
# Containers that can carry cover art as an attached picture stream.
COVER_ART_CONTAINERS = {".mp3", ".m4a", ".flac"}
# Containers H.264 + AAC can go into; anything else becomes .mp4 for Precise.
H264_CONTAINERS = {".mp4", ".m4v", ".mov", ".mkv"}
FASTSTART_CONTAINERS = {".mp4", ".m4v", ".mov"}


class UnsupportedEdit(ValueError):
    pass


@dataclass(frozen=True)
class Crop:
    x: int
    y: int
    w: int
    h: int

    def even(self) -> "Crop":
        """yuv420 video needs even offsets and sizes."""
        return Crop(self.x // 2 * 2, self.y // 2 * 2, max(2, self.w // 2 * 2),
                    max(2, self.h // 2 * 2))


@dataclass(frozen=True)
class EditSpec:
    source: Path
    kind: MediaKind
    mode: EditMode
    start: float | None = None  # seconds; None = from the beginning
    end: float | None = None  # seconds, exclusive; None = to the end
    crop: Crop | None = None
    audio_codec: str | None = None  # source audio codec (ffprobe codec_name)

    @property
    def is_lossless_audio(self) -> bool:
        return self.kind is MediaKind.AUDIO and self.audio_codec in LOSSLESS_AUDIO_CODECS


def output_suffix(spec: EditSpec) -> str:
    suffix = spec.source.suffix.lower()
    if spec.kind is MediaKind.VIDEO:
        if spec.mode is EditMode.PRECISE_LOSSLESS:
            return ".mkv"
        if spec.mode is EditMode.PRECISE and suffix not in H264_CONTAINERS:
            return ".mp4"
    return suffix


def can_overwrite(spec: EditSpec) -> bool:
    """Overwrite needs the same container, and never for Precise Lossless."""
    if spec.kind is MediaKind.VIDEO and spec.mode is EditMode.PRECISE_LOSSLESS:
        return False
    return output_suffix(spec) == spec.source.suffix.lower()


def copy_path(source: Path, suffix: str | None = None) -> Path:
    """name (edited).ext, then name (edited 2).ext, … next to the source."""
    suffix = suffix or source.suffix
    candidate = source.with_name(f"{source.stem} (edited){suffix}")
    n = 2
    while candidate.exists():
        candidate = source.with_name(f"{source.stem} (edited {n}){suffix}")
        n += 1
    return candidate


def temp_path(source: Path, suffix: str | None = None) -> Path:
    return source.with_name(f"{source.stem}.glancer-tmp{suffix or source.suffix}")


def _trim_args(spec: EditSpec) -> tuple[list[str], list[str]]:
    """(input options before -i, output options)."""
    before: list[str] = []
    after: list[str] = []
    start = spec.start if spec.start and spec.start > 0 else 0.0
    if start > 0:
        before += ["-ss", f"{start:.6f}"]
    if spec.end is not None:
        after += ["-t", f"{max(0.0, spec.end - start):.6f}"]
    return before, after


def build_command(ffmpeg: Path | str, spec: EditSpec, output: Path) -> list[str]:
    before, trim = _trim_args(spec)
    command = [str(ffmpeg), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1",
               "-nostats", *before, "-i", str(spec.source), *trim]
    suffix = output.suffix.lower()

    if spec.kind is MediaKind.VIDEO:
        command += ["-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0"]
        crop = spec.crop.even() if spec.crop else None
        if spec.mode is EditMode.FAST_LOSSLESS:
            if crop:
                raise UnsupportedEdit("Fast Lossless copies the video stream, so it can't crop.")
            command += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
        else:
            if crop:
                command += ["-vf", f"crop={crop.w}:{crop.h}:{crop.x}:{crop.y}"]
            if spec.mode is EditMode.PRECISE:
                command += ["-c:v", "libx264", "-crf", "18", "-preset", "slow",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
            else:
                command += ["-c:v", "ffv1", "-level", "3", "-g", "1", "-c:a", "flac"]
        if suffix in FASTSTART_CONTAINERS and spec.mode is not EditMode.FAST_LOSSLESS:
            command += ["-movflags", "+faststart"]

    elif spec.kind is MediaKind.AUDIO:
        command += ["-map", "0:a:0"]
        if suffix in COVER_ART_CONTAINERS:
            command += ["-map", "0:v?", "-c:v", "copy"]
        command += ["-map_metadata", "0"]
        codec = spec.audio_codec or ""
        if spec.is_lossless_audio:
            command += ["-c:a", codec]  # same lossless codec: exact, no quality loss
        elif spec.mode is EditMode.FAST_LOSSLESS:
            command += ["-c:a", "copy"]
        elif codec in AUDIO_ENCODERS:
            command += AUDIO_ENCODERS[codec]
        else:
            raise UnsupportedEdit(f"Glancer can't re-encode {codec or 'this'} audio.")
    else:
        raise UnsupportedEdit("Photos are cropped without ffmpeg.")

    return command + [str(output)]
