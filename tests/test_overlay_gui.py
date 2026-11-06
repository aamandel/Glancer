"""Phase 2 overlay UI: layout per media kind, auto-hide, H-lock, clicks, keys.

Mouse input is sent as synthetic Qt events, so the real cursor never moves.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from PIL import Image

from glancer import file_ops, zoom
from glancer.main_window import MainWindow
from glancer.media_types import MediaKind
from glancer.settings import SortKey
from glancer.zoom import ZoomState
from tests.conftest import process_events, wait_until




def load(window: MainWindow, path: Path) -> None:
    loaded: list[MediaKind] = []
    window.player.file_loaded.connect(lambda _p, k: loaded.append(k))
    window.open_file(path)
    assert wait_until(lambda: bool(loaded), 8), f"{path.name} did not load"
    process_events(0.1)


def mouse(window: MainWindow, kind: QEvent.Type, button=Qt.MouseButton.NoButton) -> None:  # type: ignore[no-untyped-def]
    pos = QPointF(window.view.width() / 2, window.view.height() / 3)
    buttons = Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress else button
    event = QMouseEvent(
        kind, pos, window.view.mapToGlobal(pos), button, buttons, Qt.KeyboardModifier.NoModifier
    )
    QApplication.sendEvent(window.view, event)


def move_mouse(window: MainWindow) -> None:
    mouse(window, QEvent.Type.MouseMove)


def click(window: MainWindow) -> None:
    mouse(window, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton)
    mouse(window, QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton)


def double_click(window: MainWindow) -> None:
    click(window)
    mouse(window, QEvent.Type.MouseButtonDblClick, Qt.MouseButton.LeftButton)
    mouse(window, QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton)


def settle() -> None:
    process_events(0.1)


def test_layout_follows_media_kind(window: MainWindow, media) -> None:  # type: ignore[no-untyped-def]
    bar = window.bar
    assert window.empty_state.isVisible() and not bar.isVisible()

    load(window, media("frames_30fps.mp4"))
    assert not window.empty_state.isVisible() and window.overlay_visible
    assert bar.scrub_row.isVisible() and bar.transport.isVisible()
    assert not bar.photo_controls.isVisible()
    assert bar.path_label.full_text() == str(media("frames_30fps.mp4"))
    centre = bar.transport.mapTo(window, bar.transport.rect().center()).x()
    assert abs(centre - window.width() / 2) <= 2  # transport centred despite the long path

    load(window, media("corners.png"))
    assert bar.photo_controls.isVisible() and bar.zoom_label.text() == "100%"
    assert not bar.scrub_row.isVisible() and not bar.playback_extras.isVisible()

    load(window, media("tone_cover.mp3"))
    assert window.audio_info.isVisible() and window.audio_info.has_art
    assert wait_until(lambda: window.audio_info.caption.text() == "Test Tone — Glancer")
    load(window, media("tone.wav"))
    assert not window.audio_info.has_art and window.audio_info.icon.isVisible()
    assert window.audio_info.caption.text() == "tone.wav"


def test_auto_hide_and_h_lock(window: MainWindow, media) -> None:  # type: ignore[no-untyped-def]
    window.auto_hide_ms = 150
    load(window, media("frames_30fps.mp4"))
    window.player.set_paused(True)
    assert wait_until(lambda: window.player.paused)

    move_mouse(window)
    assert window.overlay_visible
    assert wait_until(lambda: not window.overlay_visible, 2)
    assert wait_until(lambda: not window.bar.isVisible(), 1)
    assert window.view.cursor().shape() == Qt.CursorShape.BlankCursor
    move_mouse(window)
    assert window.overlay_visible and window.bar.isVisible()

    QTest.keyClick(window, Qt.Key.Key_H)
    assert window.overlay_locked and not window.overlay_visible
    move_mouse(window)
    assert not window.overlay_visible  # H-lock ignores mouse movement
    click(window)
    settle()
    assert not window.overlay_locked and window.overlay_visible
    assert window.player.paused  # the unlocking click doesn't also play

    # Audio controls never auto-hide.
    load(window, media("tone.wav"))
    move_mouse(window)
    process_events(0.5)
    assert window.overlay_visible


def test_clicks_and_fullscreen(window: MainWindow, media) -> None:  # type: ignore[no-untyped-def]
    import ctypes

    load(window, media("frames_30fps.mp4"))
    window.player.set_paused(True)
    assert wait_until(lambda: window.player.paused)

    click(window)
    assert wait_until(lambda: not window.player.paused, 0.3)  # single click plays at once
    window.player.set_paused(True)
    assert wait_until(lambda: window.player.paused)

    double_click(window)  # just two clicks: play, then pause again; no fullscreen
    settle()
    assert window.player.paused and not window.isFullScreen()

    normal = window.geometry()
    screen = window.screen().geometry()
    hwnd = int(window.winId())
    covers_screen = (screen.width(), screen.height() + 1)  # + 1 row: see glancer/win/fullscreen.py
    for leave in (lambda: QTest.keyClick(window, Qt.Key.Key_Escape),
                  window.bar.fullscreen_button.click,
                  lambda: QTest.keyClick(window, Qt.Key.Key_F11)):
        QTest.keyClick(window, Qt.Key.Key_F11)
        assert window.isFullScreen()
        # An off-screen 1 px border keeps Windows compositing the window, so
        # popup menus appear over it.
        assert ctypes.windll.user32.GetWindowLongW(hwnd, -16) & 0x00800000  # WS_BORDER
        assert wait_until(lambda: window.view.size().toTuple() == covers_screen, 2), window.view.size()
        leave()
        assert not window.isFullScreen()
        assert wait_until(lambda: window.geometry() == normal, 2), window.geometry()


def test_playback_hotkeys(window: MainWindow, media) -> None:  # type: ignore[no-untyped-def]
    load(window, media("frames_30fps.mp4"))
    p = window.player
    for key in (Qt.Key.Key_BracketRight, Qt.Key.Key_BracketRight, Qt.Key.Key_BracketLeft):
        QTest.keyClick(window, key)
    assert p.speed == 1.25 and window.bar.speed_button.text() == "1.25×"
    assert p.get_property("speed") == pytest.approx(1.25)
    QTest.keyClick(window, Qt.Key.Key_Backspace)
    assert p.speed == 1.0

    QTest.keyClick(window, Qt.Key.Key_L)
    assert p.loop and window.bar.loop_button.isChecked()
    assert p.get_property("loop-file") == "inf"
    QTest.keyClick(window, Qt.Key.Key_R)
    QTest.keyClick(window, Qt.Key.Key_R)
    QTest.keyClick(window, Qt.Key.Key_R, Qt.KeyboardModifier.ShiftModifier)
    assert p.rotation == 90 and p.get_property("video-rotate") == 90

    muted = p.muted
    QTest.keyClick(window, Qt.Key.Key_M)
    assert wait_until(lambda: p.muted is not muted)
    volume = p.volume
    wheel = QWheelEvent(
        QPointF(10, 10), QPointF(window.mapToGlobal(QPoint(10, 10))), QPoint(0, 0),
        QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(window, wheel)
    assert wait_until(lambda: window.bar.volume_slider.value() == min(100, volume + 5))

    # Loop and speed carry over to the next video; rotation is per file.
    load(window, media("frames_vfr.mp4"))
    assert p.loop and p.rotation == 0


def wheel(window: MainWindow, notches: float, ctrl: bool = False) -> None:
    pos = QPointF(window.view.width() * 0.7, window.view.height() * 0.4)
    mods = Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier
    event = QWheelEvent(
        pos, window.view.mapToGlobal(pos), QPoint(0, 0), QPoint(0, round(notches * 120)),
        Qt.MouseButton.NoButton, mods, Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(window.view, event)


def test_wheel_zoom_drag_pan_and_reset(window: MainWindow, media) -> None:  # type: ignore[no-untyped-def]
    load(window, media("corners.png"))
    assert wait_until(lambda: window._media_size == (1920.0, 1080.0))
    wheel(window, 4)
    state = window.zoom_state
    assert state.percent == 244 and window.bar.zoom_label.text() == "244%"
    assert window.zoom_badge.isVisible() and window.zoom_badge.text() == "244%"
    assert window.player.get_property("video-zoom") == pytest.approx(math.log2(state.zoom))
    assert window.view.cursor().shape() == Qt.CursorShape.OpenHandCursor
    volume = window.player.volume
    wheel(window, 1, ctrl=True)  # Ctrl+wheel is volume, not zoom
    assert window.zoom_state.percent == 244
    assert wait_until(lambda: window.player.volume == min(100, volume + 5))

    # Drag right/down far: clamped so the top-left corner shows, and no click fires.
    paused = window.player.paused
    mouse(window, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton)
    for step in range(1, 6):
        pos = QPointF(window.view.width() / 2 + step * 400, window.view.height() / 3 + step * 400)
        QApplication.sendEvent(window.view, QMouseEvent(
            QEvent.Type.MouseMove, pos, window.view.mapToGlobal(pos), Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    mouse(window, QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton)
    limit_x, limit_y = zoom.max_pan(window._view_size(), (1920.0, 1080.0), state.zoom)
    assert (window.zoom_state.pan_x, window.zoom_state.pan_y) == pytest.approx((limit_x, limit_y))
    settle()
    assert window.player.paused == paused and not window.overlay_locked

    # Rotating swaps the media size used for clamping.
    QTest.keyClick(window, Qt.Key.Key_R)
    assert wait_until(lambda: window._media_size == (1080.0, 1920.0))

    # Zoom carries over to the next file; audio is shown unzoomed; 0 resets.
    load(window, media("frames_30fps.mp4"))
    assert window.zoom_state.percent == 244
    assert window.player.get_property("video-zoom") == pytest.approx(math.log2(state.zoom))
    load(window, media("tone.wav"))
    assert window.player.get_property("video-zoom") == 0 and not window.zoom_badge.isVisible()
    QTest.keyClick(window, Qt.Key.Key_0)
    assert window.zoom_state == ZoomState()


def test_folder_navigation(window: MainWindow, media, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    for source, name in [("corners.png", "a1.png"), ("exif_orient6.jpg", "a2.jpg"),
                         ("frames_30fps.mp4", "a3.mp4"), ("tone.wav", "a10.wav")]:
        shutil.copy(media(source), tmp_path / name)
    load(window, tmp_path / "a1.png")
    window._refresh_sort_menu()
    assert window._sort_source_action.text() == "Default order"  # no Explorer window here
    window._choose_sort(key=SortKey.NAME)
    assert not window.bar.previous_button.isEnabled() and window.bar.next_button.isEnabled()

    def go(key: Qt.Key, shift: bool, expected: str) -> None:
        loaded: list[Path] = []
        window.player.file_loaded.connect(lambda p, _k: loaded.append(p))
        mods = Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
        QTest.keyClick(window, key, mods)
        assert wait_until(lambda: bool(loaded), 8) and loaded[0].name == expected

    go(Qt.Key.Key_Right, False, "a2.jpg")  # plain arrows change items on photos
    go(Qt.Key.Key_Right, False, "a3.mp4")
    QTest.keyClick(window, Qt.Key.Key_Right)  # ...but step frames on video
    assert wait_until(lambda: window.player.pending_steps == 0)
    assert window.player.path == tmp_path / "a3.mp4"
    go(Qt.Key.Key_Right, True, "a10.wav")  # natural order: a3 before a10
    QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert window.toast.isVisible() and window.toast.text() == "Last item"
    assert window.player.path == tmp_path / "a10.wav"

    window._choose_sort(descending=True)
    go(Qt.Key.Key_Right, True, "a3.mp4")
    window.bar.next_button.click()
    assert wait_until(lambda: window.player.path == tmp_path / "a2.jpg", 8)


def test_delete_moves_to_next_then_previous(window: MainWindow, media, tmp_path: Path,  # type: ignore[no-untyped-def]
                                            monkeypatch) -> None:
    trash = tmp_path / "trash"
    trash.mkdir()
    folder = tmp_path / "photos"
    folder.mkdir()
    # Stand-in for the Recycle Bin so tests never fill the real one.
    monkeypatch.setattr(file_ops, "send_to_trash", lambda p: shutil.move(str(p), trash / p.name))
    for name in ("b1.png", "b2.png", "b3.png"):
        shutil.copy(media("corners.png"), folder / name)
    load(window, folder / "b2.png")
    window._choose_sort(key=SortKey.NAME)

    monkeypatch.setattr(window, "confirm_delete", lambda _p: False)
    QTest.keyClick(window, Qt.Key.Key_Delete)
    assert (folder / "b2.png").exists()  # cancelled

    monkeypatch.setattr(window, "confirm_delete", lambda _p: True)
    for key_or_menu, expected in [("key", "b3.png"), ("menu", "b1.png")]:
        loaded: list[Path] = []
        window.player.file_loaded.connect(lambda p, _k: loaded.append(p))
        current = window.player.path
        if key_or_menu == "key":
            QTest.keyClick(window, Qt.Key.Key_Delete)
        else:
            window._delete_action.trigger()
        assert wait_until(lambda: bool(loaded), 8) and loaded[0].name == expected
        assert (trash / current.name).exists() and not current.exists()
    window.delete_current()  # the last file: back to the start screen
    assert window.empty_state.isVisible() and (trash / "b1.png").exists()


def test_save_frame_and_info_panel(window: MainWindow, media, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    video = tmp_path / "clip.mp4"
    shutil.copy(media("frames_30fps.mp4"), video)
    load(window, video)
    window.player.set_paused(True)
    assert wait_until(lambda: window.player.paused)
    window.player.seek(2.0)
    assert wait_until(lambda: abs(window.player.position - 2.0) < 0.001)
    window.save_frame()
    window.save_frame()
    first, second = tmp_path / "clip_frame_000060.png", tmp_path / "clip_frame_000060 (2).png"
    assert wait_until(lambda: first.exists() and second.exists(), 5)
    with Image.open(first) as frame:
        assert frame.size == (1280, 720)  # original resolution, no overlay

    QTest.keyClick(window, Qt.Key.Key_I)
    assert window.info_panel.isVisible()
    assert wait_until(lambda: "1280 × 720" in window.info_panel.basic.text(), 5)
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert not window.info_panel.isVisible()
