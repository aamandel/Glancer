"""Editor: ffmpeg commands, frame-exact trims, photo crops, background jobs, the GUI flow."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, JpegImagePlugin
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from glancer import file_ops
from glancer.editing.commands import (
    Crop,
    EditSpec,
    UnsupportedEdit,
    build_command,
    can_overwrite,
    copy_path,
    output_suffix,
)
from glancer.editing.jobs import Job, JobRunner
from glancer.editing.save import creation_time, crop_photo, set_creation_time
from glancer.main_window import MainWindow
from glancer.media_types import MediaKind
from glancer.metadata import probe
from glancer.settings import EditMode
from glancer.utils import find_binary
from tests.conftest import process_events, wait_until

FPS = 30


@pytest.fixture
def ffmpeg() -> str:
    path = find_binary("ffmpeg.exe")
    if path is None:
        pytest.skip("ffmpeg.exe is not in bin/")
    return str(path)


def _frames(ffmpeg: str, path: Path, size: tuple[int, int], vf: str | None = None) -> list[bytes]:
    args = [ffmpeg, "-v", "error", "-i", str(path)] + (["-vf", vf] if vf else [])
    raw = subprocess.run(args + ["-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    step = size[0] * size[1] * 3
    return [raw[i:i + step] for i in range(0, len(raw), step)]


def _diff(a: bytes, b: bytes) -> float:
    a, b = a[::101], b[::101]
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def test_commands_and_copy_names(tmp_path: Path) -> None:
    video = tmp_path / "clip.mov"
    video.write_bytes(b"")
    crop = Crop(101, 51, 641, 361)
    precise = EditSpec(video, MediaKind.VIDEO, EditMode.PRECISE, 1.5, 3.0, crop)
    cmd = build_command("ffmpeg", precise, tmp_path / "out.mov")
    assert float(cmd[cmd.index("-ss") + 1]) == 1.5 and cmd.index("-ss") < cmd.index("-i")
    assert float(cmd[cmd.index("-t") + 1]) == 1.5
    assert "crop=640:360:100:50" in cmd[cmd.index("-vf") + 1]  # even, for yuv420p
    assert "libx264" in cmd and "+faststart" in cmd and can_overwrite(precise)

    lossless = EditSpec(video, MediaKind.VIDEO, EditMode.PRECISE_LOSSLESS, 1.5)
    assert output_suffix(lossless) == ".mkv" and not can_overwrite(lossless)
    assert "ffv1" in build_command("ffmpeg", lossless, tmp_path / "o.mkv")
    fast = EditSpec(video, MediaKind.VIDEO, EditMode.FAST_LOSSLESS, 1.5)
    assert "copy" in build_command("ffmpeg", fast, tmp_path / "o.mov")
    with pytest.raises(UnsupportedEdit):
        build_command("ffmpeg", EditSpec(video, MediaKind.VIDEO, EditMode.FAST_LOSSLESS,
                                         crop=crop), tmp_path / "o.mov")
    flac = EditSpec(tmp_path / "a.flac", MediaKind.AUDIO, EditMode.PRECISE, 1.0,
                    audio_codec="flac")
    assert flac.is_lossless_audio and "flac" in build_command("ffmpeg", flac, tmp_path / "o.flac")

    assert copy_path(video).name == "clip (edited).mov"
    (tmp_path / "clip (edited).mov").write_bytes(b"")
    assert copy_path(video).name == "clip (edited 2).mov"
    assert copy_path(video, ".mkv").name == "clip (edited).mkv"


def test_trims_are_frame_exact(ffmpeg: str, media, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = media("frames_30fps.mp4")
    # Keep frames 45..54: the end is exclusive, half a frame after the last kept one.
    start, end = 45 / FPS, 54 / FPS + 0.5 / FPS
    crop = Crop(100, 50, 640, 360)
    cases = [
        (EditMode.PRECISE, crop, (640, 360), "crop=640:360:100:50"),
        (EditMode.PRECISE_LOSSLESS, None, (1280, 720), None),
    ]
    for mode, crop_, size, vf in cases:
        spec = EditSpec(source, MediaKind.VIDEO, mode, start, end, crop_)
        out = tmp_path / f"{mode.value}{output_suffix(spec)}"
        subprocess.run(build_command(ffmpeg, spec, out), capture_output=True, check=True)
        got = _frames(ffmpeg, out, size)
        assert len(got) == 10, mode
        wanted = _frames(ffmpeg, source, size,
                         f"select=between(n\\,44\\,55){',' + vf if vf else ''}")
        assert _diff(got[0], wanted[1]) < 1.5 < _diff(got[0], wanted[0]), mode  # frame 45, not 44
        assert _diff(got[-1], wanted[10]) < 1.5 < _diff(got[-1], wanted[11]), mode  # 54, not 55

    # Fast Lossless can only start on a keyframe, at or before the requested start.
    fast = EditSpec(source, MediaKind.VIDEO, EditMode.FAST_LOSSLESS, start, end)
    out = tmp_path / "fast.mp4"
    subprocess.run(build_command(ffmpeg, fast, out), capture_output=True, check=True)
    assert len(_frames(ffmpeg, out, (1280, 720))) >= 10


def test_photo_crop_keeps_orientation_and_quality(media, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = media("exif_orient6.jpg")
    with Image.open(source) as original:
        sampling = JpegImagePlugin.get_sampling(original)
        stored = original.size
    out = tmp_path / "cropped.jpg"
    crop_photo(source, Crop(10, 20, 300, 400), out)
    with Image.open(out) as image:
        assert image.size == (300, 400)
        assert image.getexif().get(0x0112) == 1  # upright pixels, no rotate tag
        assert JpegImagePlugin.get_sampling(image) == sampling
    assert stored[0] > stored[1]  # stored sideways; the crop is in upright coordinates


def test_job_runner_progress_and_cancel(qapp, ffmpeg: str, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    runner = JobRunner()
    progress: list[float] = []
    done: list[tuple[Job, bool]] = []
    runner.progress.connect(lambda _j, f: progress.append(f))
    runner.finished.connect(lambda j, ok: done.append((j, ok)))
    out = tmp_path / "slow.mp4"
    slow = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=60",
            "-c:v", "libx264", "-preset", "veryslow", str(out)]
    quick = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats",
             "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=1",
             "-c:v", "libx264", str(tmp_path / "quick.mp4")]
    runner.submit(Job(slow, out, 60.0, "slow"))
    runner.submit(Job(quick, tmp_path / "quick.mp4", 1.0, "quick"))
    assert wait_until(lambda: progress and out.exists(), 10)
    runner.cancel_current()
    assert wait_until(lambda: len(done) == 2, 15)
    (slow_job, slow_ok), (quick_job, quick_ok) = done
    assert not slow_ok and slow_job.error == "Cancelled." and not out.exists()
    assert quick_ok and quick_job.output.exists() and not runner.busy


def _load(window: MainWindow, path: Path) -> None:
    window.open_file(path)
    assert wait_until(lambda: window.player.path == path and window._media_size is not None, 8)
    process_events(0.1)


def test_edit_flow_copy_and_overwrite(window: MainWindow, media, tmp_path: Path,  # type: ignore[no-untyped-def]
                                      monkeypatch) -> None:
    trash = tmp_path / "trash"
    trash.mkdir()
    monkeypatch.setattr(file_ops, "send_to_trash", lambda p: shutil.move(str(p), trash / p.name))
    video = tmp_path / "clip.mp4"
    shutil.copy(media("frames_30fps.mp4"), video)
    _load(window, video)

    # Video: I / O at frames 30 and 59, Precise, save a copy in the background.
    QTest.keyClick(window, Qt.Key.Key_E)
    editor = window.editor
    assert editor.active and editor.panel.isVisible() and wait_until(lambda: window.player.paused)
    assert not editor.overlay.isHidden() and editor.pixel_size == (1280, 720)
    for seconds, key in ((1.0, Qt.Key.Key_I), (59 / FPS, Qt.Key.Key_O)):
        window.player.seek(seconds)
        assert wait_until(lambda: abs(window.player.position - seconds) < 0.001)
        QTest.keyClick(window, key)
    assert window.bar.seek_slider.markers[0] == pytest.approx(0.1)
    QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert window.player.path == video  # no navigating away mid-edit
    editor.panel.copy_button.click()
    assert not editor.active and window.jobs.busy
    copy = tmp_path / "clip (edited).mp4"
    assert wait_until(lambda: not window.jobs.busy, 15) and copy.exists()
    assert float(probe(copy)["format"]["duration"]) == pytest.approx(1.0, abs=0.05)

    # Photo: 1:1 crop, overwrite; the original goes to the "Recycle Bin".
    photo = tmp_path / "photo.jpg"
    shutil.copy(media("exif_orient6.jpg"), photo)
    set_creation_time(photo, 1_600_000_000)
    _load(window, photo)
    window.begin_edit()
    width, height = editor.pixel_size
    editor.panel.aspect_box.setCurrentIndex(5)  # 1:1
    assert editor.overlay.current().w == editor.overlay.current().h == min(width, height)
    monkeypatch.setattr(window, "confirm_overwrite", lambda _p: False)
    editor.panel.overwrite_button.click()
    assert editor.active and not (trash / "photo.jpg").exists()  # declined
    monkeypatch.setattr(window, "confirm_overwrite", lambda _p: True)
    editor.panel.overwrite_button.click()
    assert (trash / "photo.jpg").exists() and not editor.active
    with Image.open(photo) as image:
        assert image.size == (min(width, height),) * 2
    assert abs(creation_time(photo) - 1_600_000_000) < 1 and os.path.getmtime(photo) > 1.7e9
    assert wait_until(lambda: window.player.path == photo)
