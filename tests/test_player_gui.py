"""Integration tests: the real window, libmpv and OpenGL rendering.

These need a desktop session and the generated media in tests/media.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterator

import pytest
from PySide6.QtCore import QSettings

from glancer.main_window import MainWindow
from glancer.media_types import MediaKind
from glancer.settings import Settings
from tests.conftest import process_events, wait_until

FRAME = 1 / 30


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(QSettings(str(tmp_path / "glancer.ini"), QSettings.Format.IniFormat))


@pytest.fixture
def window(qapp, settings: Settings) -> Iterator[MainWindow]:  # type: ignore[no-untyped-def]
    win = MainWindow(settings=settings)
    win.resize(800, 560)
    win.show()
    process_events(0.2)
    yield win
    win.close()
    process_events(0.1)


def load(window: MainWindow, path: Path) -> MediaKind:
    loaded: list[MediaKind] = []
    window.player.file_loaded.connect(lambda _p, k: loaded.append(k))
    window.open_file(path)
    assert wait_until(lambda: bool(loaded), 8), f"{path.name} did not load"
    return loaded[0]


def distinct_colours(window: MainWindow) -> int:
    image = window.view.grabFramebuffer()
    return len(
        {
            image.pixel(x, y)
            for x in range(0, image.width(), 23)
            for y in range(0, image.height(), 23)
        }
    )


def seek_paused(window: MainWindow, seconds: float) -> None:
    window.player.set_paused(True)
    assert wait_until(lambda: window.player.paused)
    window.player.seek(seconds)
    assert wait_until(lambda: abs(window.player.position - seconds) < 0.001, 5)
    process_events(0.1)


# -- loading and rendering ----------------------------------------------------


def test_every_kind_loads_and_renders(window: MainWindow, media) -> None:  # type: ignore[no-untyped-def]
    cases = {
        "frames_30fps.mp4": MediaKind.VIDEO,
        "rotated_portrait.mp4": MediaKind.VIDEO,
        "exif_orient6.jpg": MediaKind.IMAGE,
        "corners.png": MediaKind.IMAGE,
        "animated.gif": MediaKind.ANIMATED,
        "animated.webp": MediaKind.ANIMATED,
        "tone_cover.mp3": MediaKind.AUDIO,
    }
    for name, kind in cases.items():
        assert load(window, media(name)) is kind, name
        assert wait_until(lambda: distinct_colours(window) > 3, 5), f"{name}: nothing rendered"
        assert window.windowTitle() == f"{name} — Glancer"
    # Audio without cover art has nothing to draw but must still load.
    assert load(window, media("tone.wav")) is MediaKind.AUDIO
    assert wait_until(lambda: window.player.duration > 7.9)


def test_exif_rotation_is_applied(window: MainWindow, media) -> None:  # type: ignore[no-untyped-def]
    sizes: list[tuple[int, int]] = []
    window.player.video_size_changed.connect(lambda w, h: sizes.append((w, h)))
    load(window, media("exif_orient6.jpg"))
    assert wait_until(lambda: bool(sizes))
    assert sizes[-1] == (1200, 1600)  # stored 1600x1200, shown portrait


def test_bad_files_are_reported(window: MainWindow, media, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    errors: list[str] = []
    window.player.load_failed.connect(errors.append)
    window.open_file(media("broken.mp4"))
    assert wait_until(lambda: bool(errors), 5)
    assert "could not be opened" in errors[0] and window.windowTitle() == "Glancer"

    # mpv itself would open this (a PNG inside), so the extension check matters,
    # and the current file must stay open.
    load(window, media("corners.png"))
    fake = tmp_path / "model.3mf"
    shutil.copy(media("corners.png"), fake)
    window.open_file(fake)
    assert errors[-1] == "Glancer can't open .3mf files."
    assert window.player.path == media("corners.png")


def test_saved_volume_applies_before_playback(qapp, settings: Settings, media) -> None:  # type: ignore[no-untyped-def]
    settings.volume = 23
    settings.muted = True
    win = MainWindow(settings=settings)
    try:
        assert win.player.volume == 23 and win.player.muted
        seen: list[int] = []
        win.player.volume_changed.connect(seen.append)
        win.show()
        load(win, media("frames_30fps.mp4"))
        process_events(0.3)
        assert all(v == 23 for v in seen) and win.player.volume == 23
    finally:
        win.close()


# -- frame stepping -----------------------------------------------------------


def step_like_held_key(window: MainWindow, direction: int, count: int) -> None:
    for _ in range(count):  # ~30 Hz key repeat
        window.arrow(direction)
        process_events(0.033)
    assert wait_until(lambda: window.player.pending_steps == 0, 5)


def test_held_key_steps_are_exact_and_stay_paused(window: MainWindow, media) -> None:  # type: ignore[no-untyped-def]
    load(window, media("frames_30fps.mp4"))
    assert wait_until(lambda: not window.player.paused)  # videos autoplay
    seek_paused(window, 1.0)
    step_like_held_key(window, 1, 20)
    assert window.player.paused
    assert window.player.position == pytest.approx(1.0 + 20 * FRAME, abs=0.002)
    step_like_held_key(window, -1, 20)
    assert window.player.paused
    assert window.player.position == pytest.approx(1.0, abs=0.002)

    # An instant burst is capped (no long run-on after a key is released).
    for _ in range(50):
        window.player.step(1)
    assert wait_until(lambda: window.player.pending_steps == 0, 5)
    assert window.player.paused
    assert 1 <= round((window.player.position - 1.0) / FRAME) <= 9


def test_vfr_round_trip(window: MainWindow, media) -> None:  # type: ignore[no-untyped-def]
    load(window, media("frames_vfr.mp4"))
    seek_paused(window, 1.0)
    step_like_held_key(window, 1, 12)
    assert window.player.position > 1.0
    step_like_held_key(window, -1, 12)
    assert window.player.position == pytest.approx(1.0, abs=0.002)


def test_arrow_keys_by_kind(window: MainWindow, media) -> None:  # type: ignore[no-untyped-def]
    load(window, media("tone.wav"))
    seek_paused(window, 1.0)
    window.arrow(1)
    assert wait_until(lambda: window.player.position == pytest.approx(6.0, abs=0.05), 3)
    window.arrow(-1)
    assert wait_until(lambda: window.player.position == pytest.approx(1.0, abs=0.05), 3)

    load(window, media("corners.png"))  # photos: next/previous item arrives in Phase 4
    window.arrow(1)
    assert window.player.pending_steps == 0 and window.player.path == media("corners.png")
