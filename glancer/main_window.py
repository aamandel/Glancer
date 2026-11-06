from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from glancer.mpv_controller import MpvController
from glancer.utils import SUPPORTED_VIDEO_FILTER, find_mpv, format_time


SLIDER_STEPS = 100_000


class MainWindow(QMainWindow):
    def __init__(self, initial_video: Path | None = None):
        super().__init__()
        self._controller: MpvController | None = None
        self._initial_video = initial_video
        self._duration = 0.0
        self._position = 0.0
        self._paused = True
        self._seeking = False

        self.setWindowTitle("Glancer")
        self.resize(960, 620)
        self.setMinimumSize(640, 420)
        self._build_ui()
        self._build_shortcuts()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        if self._controller is None:
            self._start_player()

    def _build_ui(self) -> None:
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.video_surface = QWidget(root)
        self.video_surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.video_surface.setStyleSheet("background: #101010;")
        self.video_surface.setMinimumHeight(250)
        layout.addWidget(self.video_surface, 1)

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
        self.open_button = QPushButton("Open video", root)
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
        self.statusBar().showMessage("Open a video to begin")

        self.open_button.clicked.connect(self.open_video_dialog)
        self.back_button.clicked.connect(self.step_backward)
        self.play_button.clicked.connect(self.toggle_pause)
        self.forward_button.clicked.connect(self.step_forward)
        self.scrubber.sliderPressed.connect(self._begin_seek)
        self.scrubber.sliderMoved.connect(self._preview_seek)
        self.scrubber.sliderReleased.connect(self._finish_seek)
        self._set_playback_controls_enabled(False)

    def _build_shortcuts(self) -> None:
        open_action = QAction("Open", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_video_dialog)
        self.addAction(open_action)

        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.toggle_pause)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=self.step_forward)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=self.step_backward)
        QShortcut(QKeySequence("."), self, activated=self.step_forward)
        QShortcut(QKeySequence(","), self, activated=self.step_backward)

    def _start_player(self) -> None:
        mpv_path = find_mpv()
        if mpv_path is None:
            QMessageBox.critical(
                self,
                "mpv is missing",
                "Glancer could not find mpv.exe.\n\n"
                "Place mpv.exe in the Glancer folder or its bin folder, "
                "then reopen the application.",
            )
            return

        self._controller = MpvController(mpv_path, int(self.video_surface.winId()), self)
        self._controller.connected.connect(self._player_connected)
        self._controller.property_changed.connect(self._property_changed)
        self._controller.playback_error.connect(self._show_playback_error)
        self._controller.stopped.connect(self._player_stopped)
        self._controller.start()

    def _player_connected(self) -> None:
        if self._initial_video is not None:
            video, self._initial_video = self._initial_video, None
            self.open_video(video)

    def open_video_dialog(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open video", "", SUPPORTED_VIDEO_FILTER
        )
        if filename:
            self.open_video(Path(filename))

    def open_video(self, path: Path) -> None:
        if self._controller is None or not self._controller.is_running:
            self._show_playback_error("The playback engine is not running.")
            return
        self._duration = 0
        self._position = 0
        self.scrubber.setValue(0)
        self.current_time.setText(format_time(0))
        self.total_time.setText(format_time(0))
        self.statusBar().showMessage(f"Loading {path.name}…")
        self._controller.load(path)

    def _property_changed(self, name: str, value: object) -> None:
        if name == "duration":
            self._duration = float(value or 0)
            self.total_time.setText(format_time(self._duration))
            self.scrubber.setEnabled(self._duration > 0)
        elif name == "time-pos":
            self._position = float(value or 0)
            if not self._seeking:
                self.current_time.setText(format_time(self._position))
                slider_value = (
                    round(self._position / self._duration * SLIDER_STEPS)
                    if self._duration > 0
                    else 0
                )
                self.scrubber.setValue(max(0, min(SLIDER_STEPS, slider_value)))
        elif name == "pause":
            self._paused = bool(value)
            self.play_button.setText("Play" if self._paused else "Pause")
        elif name == "path":
            loaded = bool(value)
            self._set_playback_controls_enabled(loaded)
            if loaded:
                path = Path(str(value))
                self.setWindowTitle(f"{path.name} — Glancer")
                self.statusBar().showMessage(str(path))

    def _set_playback_controls_enabled(self, enabled: bool) -> None:
        self.back_button.setEnabled(enabled)
        self.play_button.setEnabled(enabled)
        self.forward_button.setEnabled(enabled)

    def toggle_pause(self) -> None:
        if self._controller is not None and self.play_button.isEnabled():
            self._controller.toggle_pause()

    def step_forward(self) -> None:
        if self._controller is not None and self.forward_button.isEnabled():
            self._controller.set_paused(True)
            self._controller.step_forward()

    def step_backward(self) -> None:
        if self._controller is not None and self.back_button.isEnabled():
            self._controller.set_paused(True)
            self._controller.step_backward()

    def _begin_seek(self) -> None:
        self._seeking = True

    def _preview_seek(self, value: int) -> None:
        if self._duration > 0:
            self.current_time.setText(format_time(value / SLIDER_STEPS * self._duration))

    def _finish_seek(self) -> None:
        self._seeking = False
        if self._controller is not None and self._duration > 0:
            seconds = self.scrubber.value() / SLIDER_STEPS * self._duration
            self._controller.seek(seconds)

    def _show_playback_error(self, message: str) -> None:
        QMessageBox.critical(self, "Playback error", message)

    def _player_stopped(self) -> None:
        self._set_playback_controls_enabled(False)
        self.scrubber.setEnabled(False)
        if self.isVisible():
            self.statusBar().showMessage("Playback engine stopped")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._controller is not None:
            self._controller.close()
        event.accept()
        QApplication.quit()
