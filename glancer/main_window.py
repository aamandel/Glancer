"""Main window: full-window media with a floating, auto-hiding control bar.

Layers inside the media area, bottom to top:

    GlancerView (mpv frames)  ->  AudioInfo  ->  EmptyState  ->  ControlBar  ->  Toast

Overlay visibility:

    mouse moves         -> show bar + cursor, restart the 2 s idle timer
    idle timer fires    -> fade bar out and hide cursor, unless: audio, mouse over
                           the bar, a menu is open, or the user is scrubbing
    H                   -> "locked": bar hidden and mouse movement ignored
    click on media / H  -> unlock and show the bar

Zoom (zoom.py) is relative to fit, 100-1600%, and kept across files for the
session; 0 resets it. The wheel zooms around the cursor; left-drag pans.

Keys are handled in keyPressEvent rather than QShortcut so held keys repeat
and nothing depends on which child has focus (overlay controls take no focus).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QCloseEvent, QKeyEvent, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsOpacityEffect,
    QMainWindow,
    QMenu,
    QWidget,
)

from glancer.media_types import MediaKind, open_dialog_filter
from glancer.player.mpv_player import MpvPlayer
from glancer.player.view import GlancerView
from glancer.settings import Settings
from glancer.ui.control_bar import ControlBar
from glancer.ui.overlays import AudioInfo, ClickClassifier, EmptyState, Toast, ZoomBadge
from glancer import zoom
from glancer.zoom import ZoomState

AUDIO_ARROW_SEEK_SECONDS = 5.0
AUTO_HIDE_MS = 2000
FADE_MS = 160
BAR_MARGIN = 8
VOLUME_WHEEL_STEP = 5
SCRUB_PREVIEW_MS = 60
KeyHandler = Callable[[], None]
ZOOMABLE = (MediaKind.VIDEO, MediaKind.ANIMATED, MediaKind.IMAGE)


class MediaArea(QWidget):
    resized = Signal()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.resized.emit()


class MainWindow(QMainWindow):
    def __init__(self, initial_file: Path | None = None, settings: Settings | None = None):
        super().__init__()
        self._settings = settings if settings is not None else Settings()
        self._overlay_visible = False
        self._overlay_locked = False
        self._menus_open = 0
        self._normal_geometry: QByteArray | None = None
        self._was_maximized = False
        self._tags: dict[str, str] = {}
        self._scrub_target: float | None = None
        self._zoom = ZoomState()  # kept across files for the session
        self._media_size: tuple[float, float] | None = None
        self._cursor_hidden = False
        self.auto_hide_ms = AUTO_HIDE_MS

        self.player = MpvPlayer(self._settings.volume, self._settings.muted, self)
        self.setWindowTitle("Glancer")
        self.setMinimumSize(640, 420)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(960, 620)
        geometry = self._settings.window_geometry
        if geometry is not None:
            self.restoreGeometry(geometry)

        self._build_ui()
        self._keys = self._build_keys()
        self._connect_player()
        QApplication.instance().installEventFilter(self)

        if initial_file is not None:
            QTimer.singleShot(0, lambda: self.open_file(initial_file))

    # -- UI -------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.media_area = MediaArea(self)
        self.media_area.setMouseTracking(True)
        self.setCentralWidget(self.media_area)

        self.view = GlancerView(self.player, self.media_area)
        self.view.setMouseTracking(True)
        self.audio_info = AudioInfo(self.media_area)
        self.audio_info.hide()
        self.empty_state = EmptyState(self.media_area)
        self.bar = ControlBar(self.media_area)
        for child in [self.bar, *self.bar.findChildren(QWidget)]:
            child.setMouseTracking(True)
        self.zoom_badge = ZoomBadge(self.media_area)
        self.toast = Toast(self.media_area)

        self._bar_opacity = QGraphicsOpacityEffect(self.bar)
        self._bar_opacity.setOpacity(0.0)
        self.bar.setGraphicsEffect(self._bar_opacity)
        self.bar.hide()
        self._fade = QPropertyAnimation(self._bar_opacity, b"opacity", self)
        self._fade.setDuration(FADE_MS)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._fade_finished)

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._idle)
        self._scrub_timer = QTimer(self)
        self._scrub_timer.setSingleShot(True)
        self._scrub_timer.setInterval(SCRUB_PREVIEW_MS)
        self._scrub_timer.timeout.connect(self._send_scrub_preview)

        self.clicks = ClickClassifier(self.view)
        self.clicks.clicked.connect(self._media_clicked)
        self.clicks.double_clicked.connect(self.toggle_fullscreen)
        self.clicks.dragged.connect(self._dragged)
        self.clicks.drag_changed.connect(lambda _dragging: self._update_cursor())
        self.view.installEventFilter(self)

        self.menu = self._build_menu()
        self.bar.menu_button.setMenu(self.menu)
        for menu in (self.menu, self.bar.speed_menu):
            menu.aboutToShow.connect(self._menu_shown)
            menu.aboutToHide.connect(self._menu_hidden)

        b = self.bar
        b.back_clicked.connect(lambda: self.arrow(-1))
        b.forward_clicked.connect(lambda: self.arrow(1))
        b.play_clicked.connect(self.player.toggle_pause)
        b.rotate_clicked.connect(lambda: self.player.rotate(90))
        b.fullscreen_clicked.connect(self.toggle_fullscreen)
        b.mute_clicked.connect(lambda: self.player.set_muted(not self.player.muted))
        b.volume_requested.connect(self.player.set_volume)
        b.speed_requested.connect(self.player.set_speed)
        b.loop_requested.connect(self.player.set_loop)
        b.seek_previewed.connect(self._scrub_preview)
        b.seek_finished.connect(self._scrub_finish)
        self.empty_state.open_clicked.connect(self.open_file_dialog)

        self.media_area.resized.connect(self._layout_overlays)
        self._show_empty_state(True)

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        # Shortcut text after a tab is only displayed; keys are handled in keyPressEvent.
        menu.addAction("Open…\tCtrl+O", self.open_file_dialog)
        menu.addSeparator()
        self._rotate_right = menu.addAction("Rotate right\tR", lambda: self.player.rotate(90))
        self._rotate_left = menu.addAction("Rotate left\tShift+R", lambda: self.player.rotate(-90))
        menu.addSeparator()
        menu.addAction("Hide controls\tH", self.toggle_overlay_lock)
        menu.addAction("Fullscreen\tF11", self.toggle_fullscreen)
        return menu

    def _build_keys(self) -> dict[tuple[int, bool, bool], KeyHandler]:
        """(key, shift, ctrl) -> handler."""
        K = Qt.Key
        p = self.player
        return {
            (K.Key_Space, False, False): p.toggle_pause,
            (K.Key_Right, False, False): lambda: self.arrow(1),
            (K.Key_Left, False, False): lambda: self.arrow(-1),
            (K.Key_Period, False, False): lambda: p.step(1),
            (K.Key_Comma, False, False): lambda: p.step(-1),
            (K.Key_O, False, True): self.open_file_dialog,
            (K.Key_F11, False, False): self.toggle_fullscreen,
            (K.Key_Escape, False, False): self.exit_fullscreen,
            (K.Key_H, False, False): self.toggle_overlay_lock,
            (K.Key_M, False, False): lambda: p.set_muted(not p.muted),
            (K.Key_L, False, False): lambda: p.set_loop(not p.loop),
            (K.Key_R, False, False): lambda: p.rotate(90),
            (K.Key_R, True, False): lambda: p.rotate(-90),
            (K.Key_BracketRight, False, False): lambda: p.change_speed(1),
            (K.Key_BracketLeft, False, False): lambda: p.change_speed(-1),
            (K.Key_Backspace, False, False): lambda: p.set_speed(1.0),
            (K.Key_0, False, False): self.reset_zoom,
        }

    def _connect_player(self) -> None:
        p, b = self.player, self.bar
        p.file_loaded.connect(self._file_loaded)
        p.load_failed.connect(self._load_failed)
        p.duration_changed.connect(b.set_duration)
        p.position_changed.connect(b.set_position)
        p.pause_changed.connect(b.set_paused)
        p.volume_changed.connect(b.set_volume)
        p.mute_changed.connect(b.set_muted)
        p.speed_changed.connect(b.set_speed)
        p.loop_changed.connect(b.set_loop)
        p.metadata_changed.connect(self._metadata_changed)
        p.video_size_changed.connect(self._media_size_changed)
        p.volume_changed.connect(self._remember_volume)
        p.mute_changed.connect(self._remember_mute)
        b.set_volume(p.volume)
        b.set_muted(p.muted)

    # -- layout ---------------------------------------------------------------

    def _layout_overlays(self) -> None:
        w, h = self.media_area.width(), self.media_area.height()
        self.view.setGeometry(0, 0, w, h)
        bar_height = self.bar.sizeHint().height()
        bar_top = h - bar_height - BAR_MARGIN
        self.bar.setGeometry(BAR_MARGIN, bar_top, w - 2 * BAR_MARGIN, bar_height)
        self.empty_state.adjustSize()
        self.empty_state.move(
            (w - self.empty_state.width()) // 2, (h - self.empty_state.height()) // 2
        )
        if self.toast.isVisible():
            self.toast.move((w - self.toast.width()) // 2, 24)
        self.zoom_badge.move(w - self.zoom_badge.width() - 16, 16)
        if self.player.kind is MediaKind.AUDIO:
            self._layout_audio(w, h, bar_top)
        else:
            self._set_zoom(self._zoom)  # re-clamp pan for the new view size

    def _layout_audio(self, w: int, h: int, bar_top: int) -> None:
        """Cover art in a centred box above the caption; the bar stays clear of it."""
        caption_height = self.audio_info.caption.sizeHint().height()
        art_top = round(h * 0.07)
        art_bottom = max(art_top + 40, bar_top - caption_height - 28)
        self.audio_info.layout_in(art_top, art_bottom, w)
        if h > 0:
            side = 0.1
            self.player.set_video_margins(side, art_top / h, side, (h - art_bottom) / h)

    def _show_empty_state(self, empty: bool) -> None:
        self.empty_state.setVisible(empty)
        if empty:
            self.audio_info.hide()
            self.zoom_badge.hide()
            self._set_overlay(False, animate=False)
            self._cursor_hidden = False
            self._update_cursor()

    # -- overlay visibility -------------------------------------------------------

    @property
    def overlay_visible(self) -> bool:
        return self._overlay_visible

    @property
    def overlay_locked(self) -> bool:
        return self._overlay_locked

    def _set_overlay(self, visible: bool, animate: bool = True) -> None:
        if visible and self.player.kind is None:
            visible = False
        if visible == self._overlay_visible and not (visible and self.bar.isHidden()):
            return
        self._overlay_visible = visible
        self._fade.stop()
        if visible:
            self.bar.show()
            self.bar.raise_()
            self.toast.raise_()
        target = 1.0 if visible else 0.0
        if animate:
            self._fade.setStartValue(self._bar_opacity.opacity())
            self._fade.setEndValue(target)
            self._fade.start()
        else:
            self._bar_opacity.setOpacity(target)
            self._fade_finished()

    def _fade_finished(self) -> None:
        if not self._overlay_visible:
            self.bar.hide()

    def user_activity(self) -> None:
        """Mouse moved: show the controls and cursor, restart the idle timer."""
        if self._cursor_hidden:
            self._cursor_hidden = False
            self._update_cursor()
        if not self._overlay_locked:
            self._set_overlay(True)
        self._idle_timer.start(self.auto_hide_ms)

    def _idle(self) -> None:
        kind = self.player.kind
        if kind is None or kind is MediaKind.AUDIO:
            return
        if self.bar.underMouse() or self._menus_open or self.bar.scrubbing:
            self._idle_timer.start(self.auto_hide_ms)
            return
        self._set_overlay(False)
        self._cursor_hidden = True
        self._update_cursor()

    def toggle_overlay_lock(self) -> None:
        if self._overlay_locked:
            self._overlay_locked = False
            self.user_activity()
        else:
            self._overlay_locked = True
            self._set_overlay(False)
        self._set_zoom(self._zoom)  # the badge hides while locked

    def _menu_shown(self) -> None:
        self._menus_open += 1

    def _menu_hidden(self) -> None:
        self._menus_open = max(0, self._menus_open - 1)
        self._idle_timer.start(self.auto_hide_ms)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseMove and isinstance(obj, QWidget):
            if obj.window() is self:
                self.user_activity()
        elif obj is self.view and event.type() == QEvent.Type.Wheel:
            assert isinstance(event, QWheelEvent)
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._wheel_volume(event)
            else:
                self._wheel_zoom(event)
            return True
        return False

    # -- zoom and pan -------------------------------------------------------------

    @property
    def zoom_state(self) -> ZoomState:
        return self._zoom

    def _zoomable(self) -> bool:
        return self.player.kind in ZOOMABLE

    def _view_size(self) -> tuple[float, float]:
        return float(self.view.width()), float(self.view.height())

    def _wheel_zoom(self, event: QWheelEvent) -> None:
        notches = event.angleDelta().y() / 120
        if not notches or not self._zoomable() or self._media_size is None:
            return
        anchor = event.position()
        self._set_zoom(
            zoom.zoom_at(
                self._zoom, self._view_size(), self._media_size,
                zoom.wheel_factor(notches), (anchor.x(), anchor.y()),
            ),
            announce=True,
        )

    def _dragged(self, dx: float, dy: float) -> None:
        if self._zoom.is_zoomed and self._zoomable() and self._media_size is not None:
            self._set_zoom(zoom.pan_by(self._zoom, self._view_size(), self._media_size, dx, dy))

    def reset_zoom(self) -> None:
        self._set_zoom(ZoomState(), announce=True)

    def _media_size_changed(self, width: int, height: int) -> None:
        self._media_size = (float(width), float(height))
        self._set_zoom(self._zoom)

    def _set_zoom(self, state: ZoomState, announce: bool = False) -> None:
        """Clamp, store and apply a zoom state; audio always shows unzoomed."""
        if self._media_size is not None:
            state = zoom.clamp(state, self._view_size(), self._media_size)
        changed = state.percent != self._zoom.percent
        self._zoom = state
        if self._zoomable():
            self.player.set_view_transform(*zoom.mpv_values(state))
        else:
            self.player.set_view_transform(0.0, 0.0, 0.0)
        self.bar.set_zoom(state.percent)
        self.zoom_badge.set_percent(state.percent)
        self.zoom_badge.setVisible(
            state.is_zoomed and self._zoomable() and not self._overlay_locked
        )
        if announce and changed and self._overlay_locked:
            self.toast.show_message(f"{state.percent}%")
        self._update_cursor()

    def _update_cursor(self) -> None:
        if self._cursor_hidden:
            self.view.setCursor(Qt.CursorShape.BlankCursor)
        elif self._zoom.is_zoomed and self._zoomable():
            self.view.setCursor(
                Qt.CursorShape.ClosedHandCursor if self.clicks.dragging
                else Qt.CursorShape.OpenHandCursor
            )
        else:
            self.view.unsetCursor()

    # -- mouse on the media -------------------------------------------------------

    def _media_clicked(self) -> None:
        if self.player.kind is None:
            return
        if self._overlay_locked:
            self.toggle_overlay_lock()
            return
        if self.player.kind.has_timeline:
            self.player.toggle_pause()
        self.user_activity()

    def wheelEvent(self, event: QWheelEvent) -> None:
        # Wheel over the bar or other overlays (the media view filters its own).
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._wheel_volume(event)
            event.accept()
            return
        super().wheelEvent(event)

    def _wheel_volume(self, event: QWheelEvent) -> None:
        notches = event.angleDelta().y() / 120
        if notches:
            self.player.set_volume(self.player.volume + round(notches * VOLUME_WHEEL_STEP))

    # -- fullscreen ---------------------------------------------------------------

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.exit_fullscreen()
        else:
            self._normal_geometry = self.saveGeometry()
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
            self.bar.set_fullscreen(True)

    def exit_fullscreen(self) -> None:
        if not self.isFullScreen():
            return
        if self._was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.bar.set_fullscreen(False)

    # -- keys ---------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        mods = event.modifiers()
        key = (
            event.key(),
            bool(mods & Qt.KeyboardModifier.ShiftModifier),
            bool(mods & Qt.KeyboardModifier.ControlModifier),
        )
        handler = self._keys.get(key)
        if handler is None:
            super().keyPressEvent(event)
            return
        # Only playback and stepping keys repeat while held.
        repeatable = key[0] in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Comma,
                                Qt.Key.Key_Period, Qt.Key.Key_BracketLeft,
                                Qt.Key.Key_BracketRight)
        if not event.isAutoRepeat() or repeatable:
            handler()
        event.accept()

    # -- actions ------------------------------------------------------------------

    def open_file_dialog(self) -> None:
        start_dir = str(self.player.path.parent) if self.player.path else ""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open file", start_dir, open_dialog_filter()
        )
        if filename:
            self.open_file(Path(filename))

    def open_file(self, path: Path) -> None:
        self.player.load(path)

    def clear(self) -> None:
        """Close the current file and return to the empty start screen."""
        self.player.unload()
        self._media_size = None
        self.setWindowTitle("Glancer")
        self.bar.set_kind(None)
        self._show_empty_state(True)

    def arrow(self, direction: int) -> None:
        """Left/Right: frames on video, ±5 s on audio. (Photos: Phase 4.)"""
        kind = self.player.kind
        if kind is None:
            return
        if kind.has_frames:
            self.player.step(direction)
        elif kind is MediaKind.AUDIO:
            self.player.seek_relative(direction * AUDIO_ARROW_SEEK_SECONDS)

    # -- scrubbing ----------------------------------------------------------------

    def _scrub_preview(self, seconds: float) -> None:
        # Fast keyframe seeks while dragging, at most one per SCRUB_PREVIEW_MS.
        self._scrub_target = seconds
        if not self._scrub_timer.isActive():
            self._send_scrub_preview()
            self._scrub_timer.start()

    def _send_scrub_preview(self) -> None:
        if self._scrub_target is not None and self.bar.scrubbing:
            self.player.seek(self._scrub_target, exact=False)
        self._scrub_target = None

    def _scrub_finish(self, seconds: float) -> None:
        self._scrub_timer.stop()
        self._scrub_target = None
        self.player.seek(seconds)

    # -- player events --------------------------------------------------------------

    def _file_loaded(self, path: Path, kind: MediaKind) -> None:
        self.setWindowTitle(f"{path.name} — Glancer")
        self._show_empty_state(False)
        self.bar.set_kind(kind)
        self.bar.set_path(str(path))
        if kind is MediaKind.AUDIO:
            self.audio_info.set_info(
                AudioInfo.caption_for(self._tags, path.name), self.player.has_video_track()
            )
            self.audio_info.show()
        else:
            self.audio_info.hide()
            self.player.set_video_margins(0, 0, 0, 0)
        self._layout_overlays()
        self._set_zoom(self._zoom)
        self.user_activity()

    def _metadata_changed(self, tags: dict[str, str]) -> None:
        self._tags = tags
        path = self.player.path
        if self.player.kind is MediaKind.AUDIO and path is not None:
            self.audio_info.set_info(
                AudioInfo.caption_for(tags, path.name), self.audio_info.has_art
            )

    def _load_failed(self, message: str) -> None:
        if self.player.path is None:
            self.setWindowTitle("Glancer")
            self.bar.set_kind(None)
            self._show_empty_state(True)
        self.toast.show_message(message)

    def _remember_volume(self, volume: int) -> None:
        self._settings.volume = volume

    def _remember_mute(self, muted: bool) -> None:
        self._settings.muted = muted

    # -- shutdown -----------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        QApplication.instance().removeEventFilter(self)
        self._idle_timer.stop()
        geometry = self._normal_geometry if self.isFullScreen() else self.saveGeometry()
        if geometry is not None:
            self._settings.window_geometry = geometry
        self.view.release()
        self.player.close()
        self._settings.sync()
        event.accept()
        QApplication.quit()
