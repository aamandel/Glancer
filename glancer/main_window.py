"""Main window (Phase 1: libmpv playback with the original simple layout).

Phase 2 replaces this layout with the overlay UI.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from glancer.media_types import MediaKind, open_dialog_filter
from glancer.player.mpv_player import MpvPlayer
from glancer.player.view import GlancerView
from glancer.settings import Settings
from glancer.utils import format_time

SLIDER_STEPS = 100_000
AUDIO_ARROW_SEEK_SECONDS = 5.0


class MainWindow(QMainWindow):
    def __init__(self, initial_file: Path | None = None, settings: Settings | None = None):
        super().__init__()
        self._settings = settings if settings is not None else Settings()
        self._seeking = False

        self.player = MpvPlayer(self._settings.volume, self._settings.muted, self)
        self.setWindowTitle("Glancer")
        self.resize(960, 620)
        self.setMinimumSize(640, 420)
        self._build_ui()
        self._build_shortcuts()
        self._connect_player()

        if initial_file is not None:
            QTimer.singleShot(0, lambda: self.open_file(initial_file))

    # -- UI -------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.view = GlancerView(self.player, root)
        layout.addWidget(self.view, 1)

        timeline = QHBoxLayout()
        self.current_time = QLabel("00:00.000", root)
        self.scrubber = QSlider(Qt.Orientation.Horizontal, root)
        self.scrubber.setRange(0, SLIDER_STEPS)
        self.scrubber.setEnabled(False)
        self.total_time = QLabel("00:00.000", root)
        timeline.addWidget(self.current_time)
        timeline.addWidget(self.scrubber, 1)
        timeline.addWidget(self.total_time)
        layout.addLayout(timeline)

        controls = QHBoxLayout()
        self.open_button = QPushButton("Open file", root)
        self.back_button = QPushButton("◀  One frame", root)
        self.play_button = QPushButton("Play", root)
        self.forward_button = QPushButton("One frame  ▶", root)
        controls.addWidget(self.open_button)
        controls.addStretch(1)
        controls.addWidget(self.back_button)
        controls.addWidget(self.play_button)
        controls.addWidget(self.forward_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.setCentralWidget(root)
        self.statusBar().showMessage("Open a file to begin")

        self.open_button.clicked.connect(self.open_file_dialog)
        self.back_button.clicked.connect(lambda: self.player.step(-1))
        self.play_button.clicked.connect(self.player.toggle_pause)
        self.forward_button.clicked.connect(lambda: self.player.step(1))
        self.scrubber.sliderPressed.connect(self._begin_seek)
        self.scrubber.sliderMoved.connect(self._preview_seek)
        self.scrubber.sliderReleased.connect(self._finish_seek)
        self._update_controls(None)

    def _build_shortcuts(self) -> None:
        open_action = QAction("Open", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_file_dialog)
        self.addAction(open_action)

        def shortcut(key: QKeySequence | str, handler) -> None:  # type: ignore[no-untyped-def]
            QShortcut(QKeySequence(key), self, activated=handler, autoRepeat=True)

        shortcut(QKeySequence(Qt.Key.Key_Space), self.player.toggle_pause)
        shortcut(QKeySequence(Qt.Key.Key_Right), lambda: self.arrow(1))
        shortcut(QKeySequence(Qt.Key.Key_Left), lambda: self.arrow(-1))
        shortcut(".", lambda: self.player.step(1))
        shortcut(",", lambda: self.player.step(-1))

    def _connect_player(self) -> None:
        p = self.player
        p.file_loaded.connect(self._file_loaded)
        p.load_failed.connect(self._load_failed)
        p.duration_changed.connect(self._duration_changed)
        p.position_changed.connect(self._position_changed)
        p.pause_changed.connect(
            lambda paused: self.play_button.setText("Play" if paused else "Pause")
        )
        p.volume_changed.connect(self._remember_volume)
        p.mute_changed.connect(self._remember_mute)

    def _update_controls(self, kind: MediaKind | None) -> None:
        has_frames = kind is not None and kind.has_frames
        has_timeline = kind is not None and kind.has_timeline
        self.back_button.setEnabled(has_frames)
        self.forward_button.setEnabled(has_frames)
        self.play_button.setEnabled(has_timeline)
        self.scrubber.setEnabled(has_timeline and self.player.duration > 0)

    # -- actions --------------------------------------------------------------

    def open_file_dialog(self) -> None:
        start_dir = str(self.player.path.parent) if self.player.path else ""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open file", start_dir, open_dialog_filter()
        )
        if filename:
            self.open_file(Path(filename))

    def open_file(self, path: Path) -> None:
        self.scrubber.setValue(0)
        self.current_time.setText(format_time(0))
        self.total_time.setText(format_time(0))
        if self.player.load(path):
            self.statusBar().showMessage(f"Loading {path.name}…")

    def arrow(self, direction: int) -> None:
        """Left/Right: frames on video, ±5 s on audio. (Photos: Phase 4.)"""
        kind = self.player.kind
        if kind is None:
            return
        if kind.has_frames:
            self.player.step(direction)
        elif kind is MediaKind.AUDIO:
            self.player.seek_relative(direction * AUDIO_ARROW_SEEK_SECONDS)

    # -- player events --------------------------------------------------------

    def _file_loaded(self, path: Path, kind: MediaKind) -> None:
        self.setWindowTitle(f"{path.name} — Glancer")
        self.statusBar().showMessage(path.name)
        self._update_controls(kind)

    def _load_failed(self, message: str) -> None:
        self.statusBar().showMessage(message)
        if self.player.path is None:
            self.setWindowTitle("Glancer")
            self._update_controls(None)

    def _duration_changed(self, duration: float) -> None:
        self.total_time.setText(format_time(duration))
        self._update_controls(self.player.kind)

    def _position_changed(self, position: float) -> None:
        if self._seeking:
            return
        self.current_time.setText(format_time(position))
        duration = self.player.duration
        value = round(position / duration * SLIDER_STEPS) if duration > 0 else 0
        self.scrubber.setValue(max(0, min(SLIDER_STEPS, value)))

    def _remember_volume(self, volume: int) -> None:
        self._settings.volume = volume

    def _remember_mute(self, muted: bool) -> None:
        self._settings.muted = muted

    # -- scrubbing ------------------------------------------------------------

    def _begin_seek(self) -> None:
        self._seeking = True

    def _preview_seek(self, value: int) -> None:
        if self.player.duration > 0:
            self.current_time.setText(format_time(value / SLIDER_STEPS * self.player.duration))

    def _finish_seek(self) -> None:
        self._seeking = False
        if self.player.duration > 0:
            self.player.seek(self.scrubber.value() / SLIDER_STEPS * self.player.duration)

    # -- shutdown -------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        self.view.release()
        self.player.close()
        self._settings.sync()
        event.accept()
        QApplication.quit()
