"""Generate synthetic test media for Glancer.

Usage: python tools/make_test_media.py [output-dir]

The default output folder is tests/media (git-ignored). The files are built
with ffmpeg and Pillow so each one exercises a specific feature:

  frames_30fps.mp4     frame number burned in, keyframe every 2 s, AAC audio
  frames_vfr.mp4       variable frame rate (irregular frame timestamps)
  rotated_portrait.mp4 landscape stream carrying 90-degree rotation metadata
  tone.wav / .flac     lossless audio
  tone_cover.mp3       MP3 with embedded cover art and title/artist tags
  tone.m4a             AAC audio
  exif_orient6.jpg     EXIF orientation 6: upright "TOP" only if rotation is honoured
  corners.png          coloured corner markers for zoom/pan edge checks
  animated.gif/.webp   short animations (treated as video)
  broken.mp4           text file with a video extension (error handling)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = "C\\:/Windows/Fonts/arialbd.ttf"


def find_tool(name: str) -> str:
    bundled = ROOT / "bin" / f"{name}.exe"
    if bundled.is_file():
        return str(bundled)
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"{name} not found in bin/ or on PATH")
    return found


FFMPEG = find_tool("ffmpeg")


def ffmpeg(*args: str) -> None:
    command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args]
    subprocess.run(command, check=True)


def frame_counter_filter(rate: int) -> str:
    return (
        f"testsrc2=size=1280x720:rate={rate},"
        f"drawtext=fontfile='{FONT}':text='%{{n}}':fontsize=160:"
        "fontcolor=white:box=1:boxcolor=black@0.7:x=(w-tw)/2:y=(h-th)/2"
    )


def make_videos(out: Path) -> None:
    ffmpeg(
        "-f", "lavfi", "-i", frame_counter_filter(30),
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-t", "10", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(out / "frames_30fps.mp4"),
    )
    # Drop an irregular set of frames so the timestamps are uneven.
    ffmpeg(
        "-f", "lavfi", "-i", frame_counter_filter(30),
        "-t", "6",
        "-vf", "select='not(eq(mod(n\\,7)\\,3))*not(eq(mod(n\\,11)\\,5))'",
        "-fps_mode", "vfr", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", str(out / "frames_vfr.mp4"),
    )
    landscape = out / "_landscape.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", frame_counter_filter(30), "-t", "3",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        str(landscape),
    )
    ffmpeg(
        "-display_rotation:v:0", "90", "-i", str(landscape),
        "-c", "copy", str(out / "rotated_portrait.mp4"),
    )
    landscape.unlink()
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10", "-t", "2",
        str(out / "animated.gif"),
    )
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10", "-t", "2",
        "-loop", "0", "-c:v", "libwebp_anim", str(out / "animated.webp"),
    )
    (out / "broken.mp4").write_text("this is not a video\n", encoding="utf-8")


def make_images(out: Path) -> Path:
    font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 120)

    # Draw an upright portrait picture, then store it rotated 90 degrees
    # counter-clockwise with orientation 6 ("rotate 90 clockwise to view").
    # It only looks upright ("TOP" at the top) when a viewer honours EXIF.
    portrait = Image.new("RGB", (1200, 1600), (40, 90, 160))
    draw = ImageDraw.Draw(portrait)
    draw.text((420, 60), "TOP", fill="white", font=font)
    draw.rectangle((0, 1500, 1199, 1599), fill=(230, 50, 50))  # red bar at the bottom
    exif = Image.Exif()
    exif[0x0112] = 6
    stored = portrait.transpose(Image.Transpose.ROTATE_90)  # 1600x1200 landscape pixels
    stored.save(out / "exif_orient6.jpg", quality=92, exif=exif)

    corners = Image.new("RGB", (1920, 1080), (30, 30, 30))
    draw = ImageDraw.Draw(corners)
    size = 160
    colours = {
        (0, 0): (230, 50, 50),
        (1920 - size, 0): (50, 200, 70),
        (0, 1080 - size): (60, 110, 240),
        (1920 - size, 1080 - size): (240, 210, 40),
    }
    for (x, y), colour in colours.items():
        draw.rectangle((x, y, x + size - 1, y + size - 1), fill=colour)
    draw.rectangle((0, 0, 1919, 1079), outline="white", width=4)
    corners.save(out / "corners.png")

    cover = out / "_cover.png"
    art = Image.new("RGB", (500, 500), (120, 40, 140))
    ImageDraw.Draw(art).text((80, 180), "ART", fill="white", font=font)
    art.save(cover)
    return cover


def make_audio(out: Path, cover: Path) -> None:
    # -t must follow the last -i so it limits the output, not an input.
    tone = ["-f", "lavfi", "-i", "sine=frequency=523:sample_rate=44100"]
    ffmpeg(*tone, "-t", "8", str(out / "tone.wav"))
    ffmpeg(*tone, "-t", "8", "-c:a", "flac", str(out / "tone.flac"))
    ffmpeg(*tone, "-t", "8", "-c:a", "aac", "-b:a", "160k", str(out / "tone.m4a"))
    ffmpeg(
        *tone, "-i", str(cover), "-t", "8",
        "-map", "0:a", "-map", "1:v", "-c:a", "libmp3lame", "-b:a", "192k",
        "-c:v", "png", "-disposition:v", "attached_pic", "-id3v2_version", "3",
        "-metadata", "title=Test Tone", "-metadata", "artist=Glancer",
        str(out / "tone_cover.mp3"),
    )
    cover.unlink()


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "media"
    out.mkdir(parents=True, exist_ok=True)
    make_videos(out)
    cover = make_images(out)
    make_audio(out, cover)
    for path in sorted(out.iterdir()):
        print(f"{path.stat().st_size:>10,}  {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
