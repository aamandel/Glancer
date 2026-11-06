"""Phase 2 overlay UI: layout per media kind, auto-hide, H-lock, clicks, keys.

Mouse input is sent as synthetic Qt events, so the real cursor never moves.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from glancer.main_window import MainWindow
from glancer.media_types import MediaKind
from tests.conftest import CLICK_INTERVAL_MS, process_events, wait_until




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


def after_double_click_interval() -> None:
    process_events(CLICK_INTERVAL_MS / 1000 + 0.1)


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
    assert window.media_area.cursor().shape() == Qt.CursorShape.BlankCursor
    move_mouse(window)
    assert window.overlay_visible and window.bar.isVisible()

    window.auto_hide_ms = 5000  # outlast the double-click wait below
    QTest.keyClick(window, Qt.Key.Key_H)
    assert window.overlay_locked and not window.overlay_visible
    move_mouse(window)
    assert not window.overlay_visible  # H-lock ignores mouse movement
    click(window)
    after_double_click_interval()
    assert not window.overlay_locked and window.overlay_visible
    assert window.player.paused  # the unlocking click doesn't also play

    # Audio controls never auto-hide.
    load(window, media("tone.wav"))
    move_mouse(window)
    process_events(0.5)
    assert window.overlay_visible


def test_click_double_click_and_fullscreen(window: MainWindow, media) -> None:  # type: ignore[no-untyped-def]
    load(window, media("frames_30fps.mp4"))
    window.player.set_paused(True)
    assert wait_until(lambda: window.player.paused)

    click(window)
    after_double_click_interval()
    assert wait_until(lambda: not window.player.paused)  # single click plays
    window.player.set_paused(True)
    assert wait_until(lambda: window.player.paused)

    double_click(window)
    assert wait_until(lambda: window.isFullScreen(), 3)
    after_double_click_interval()
    assert window.player.paused  # a double-click never also toggles playback
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert wait_until(lambda: not window.isFullScreen(), 3)
    QTest.keyClick(window, Qt.Key.Key_F11)
    assert wait_until(lambda: window.isFullScreen(), 3)
    window.bar.fullscreen_button.click()
    assert wait_until(lambda: not window.isFullScreen(), 3)


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
