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

Edit mode (ui/edit_mode.py) takes keys first while active, keeps the bar
shown, and blocks navigation and delete until it is saved or cancelled.

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
from PySide6.QtGui import QActionGroup, QCloseEvent, QKeyEvent, QResizeEvent, QWheelEvent
import threading

from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QMainWindow,
    QMenu,
    QMessageBox,
    QWidget,
)

from glancer.editing.jobs import Job, JobRunner
from glancer.explorer_sort import ExplorerSortQuery
from glancer.folder import FolderNavigator
from glancer.file_ops import (
    RecycleError,
    can_recycle,
    frame_filename,
    move_to_recycle_bin,
    show_in_explorer,
)
from glancer.media_types import MediaKind
from glancer.metadata import FileInfo, read_info
from glancer.player.thread_bridge import ThreadBridge
from glancer.win.file_dialog import get_open_path
from glancer.player.mpv_player import MpvPlayer
from glancer.player.view import GlancerView
from glancer.settings import Settings, SortKey, SortOrder
from glancer.ui.control_bar import ControlBar
from glancer.ui.edit_mode import EditController, JobIndicator
from glancer.ui.overlays import (
    AudioInfo,
    ClickClassifier,
    EmptyState,
    InfoPanel,
    Toast,
    ZoomBadge,
)
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
SORT_KEY_LABELS = {
    SortKey.NAME: "Name",
    SortKey.CREATED: "Date created",
    SortKey.MODIFIED: "Date modified",
    SortKey.SIZE: "Size",
    SortKey.TYPE: "Type",
}


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
        # Folder order: an Explorer window's sort, else the saved default;
        # the Sort order menu overrides it per folder for this session.
        self.navigator = FolderNavigator(self)
        self._nav_anchor: Path | None = None  # last file opened, even if it failed
        self._explorer_queries: dict[Path, ExplorerSortQuery] = {}
        self._folder_orders: dict[Path, SortOrder] = {}
        self._order_sources: dict[Path, str] = {}
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
        self.info_panel = InfoPanel(self.media_area)
        self.info_panel.closed.connect(self.toggle_info)
        self._info_bridge: ThreadBridge[tuple[Path, FileInfo | None]] = ThreadBridge(
            self._info_ready, self
        )
        self.jobs = JobRunner(self)
        self.jobs.finished.connect(self._job_finished)
        self.editor = EditController(self)
        self.job_indicator = JobIndicator(self.media_area, self.jobs)
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
        b.previous_item_clicked.connect(lambda: self.navigate(-1))
        b.next_item_clicked.connect(lambda: self.navigate(1))
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
        self._explorer_action = menu.addAction("Show in Explorer", self.show_in_explorer)
        self._info_action = menu.addAction("File info\tI", self.toggle_info)
        self._save_frame_action = menu.addAction("Save frame as PNG", self.save_frame)
        self._edit_action = menu.addAction("Edit…\tE", self.begin_edit)
        menu.addSeparator()
        self._rotate_right = menu.addAction("Rotate right\tR", lambda: self.player.rotate(90))
        self._rotate_left = menu.addAction("Rotate left\tShift+R", lambda: self.player.rotate(-90))
        menu.addSeparator()
        menu.addMenu(self._build_sort_menu())
        menu.addSeparator()
        menu.addAction("Hide controls\tH", self.toggle_overlay_lock)
        menu.addAction("Fullscreen\tF11", self.toggle_fullscreen)
        menu.addSeparator()
        self._delete_action = menu.addAction("Delete\tDel", self.delete_current)
        menu.aboutToShow.connect(self._refresh_menu)
        return menu

    def _refresh_menu(self) -> None:
        has_file = self.player.path is not None
        editing = self.editor.active
        for action in (self._explorer_action, self._info_action, self._delete_action,
                       self._rotate_left, self._rotate_right):
            action.setEnabled(has_file and (action is self._explorer_action or not editing))
        self._edit_action.setEnabled(not editing and self.editor.can_edit(self.player.kind))
        self._save_frame_action.setEnabled(
            self.player.kind is not None and self.player.kind.has_frames
        )

    def _build_sort_menu(self) -> QMenu:
        menu = QMenu("Sort order", self)
        self._sort_source_action = menu.addAction("")
        self._sort_source_action.setEnabled(False)
        menu.addSeparator()
        self._sort_key_actions = {}
        keys = QActionGroup(menu)
        for key, label in SORT_KEY_LABELS.items():
            action = menu.addAction(label, lambda k=key: self._choose_sort(key=k))
            action.setCheckable(True)
            keys.addAction(action)
            self._sort_key_actions[key] = action
        menu.addSeparator()
        directions = QActionGroup(menu)
        self._ascending_action = menu.addAction(
            "Ascending", lambda: self._choose_sort(descending=False)
        )
        self._descending_action = menu.addAction(
            "Descending", lambda: self._choose_sort(descending=True)
        )
        for action in (self._ascending_action, self._descending_action):
            action.setCheckable(True)
            directions.addAction(action)
        menu.addSeparator()
        menu.addAction("Set as default", self._save_default_sort)
        menu.aboutToShow.connect(self._refresh_sort_menu)
        return menu

    def _build_keys(self) -> dict[tuple[int, bool, bool], KeyHandler]:
        """(key, shift, ctrl) -> handler."""
        K = Qt.Key
        p = self.player
        return {
            (K.Key_Space, False, False): p.toggle_pause,
            (K.Key_Right, False, False): lambda: self.arrow(1),
            (K.Key_Left, False, False): lambda: self.arrow(-1),
            (K.Key_Right, True, False): lambda: self.navigate(1),
            (K.Key_Left, True, False): lambda: self.navigate(-1),
            (K.Key_Period, False, False): lambda: p.step(1),
            (K.Key_Comma, False, False): lambda: p.step(-1),
            (K.Key_O, False, True): self.open_file_dialog,
            (K.Key_F11, False, False): self.toggle_fullscreen,
            (K.Key_Escape, False, False): self._escape,
            (K.Key_I, False, False): self.toggle_info,
            (K.Key_Delete, False, False): self.delete_current,
            (K.Key_H, False, False): self.toggle_overlay_lock,
            (K.Key_M, False, False): lambda: p.set_muted(not p.muted),
            (K.Key_L, False, False): lambda: p.set_loop(not p.loop),
            (K.Key_R, False, False): lambda: p.rotate(90),
            (K.Key_R, True, False): lambda: p.rotate(-90),
            (K.Key_BracketRight, False, False): lambda: p.change_speed(1),
            (K.Key_BracketLeft, False, False): lambda: p.change_speed(-1),
            (K.Key_Backspace, False, False): lambda: p.set_speed(1.0),
            (K.Key_0, False, False): self.reset_zoom,
            (K.Key_E, False, False): self.begin_edit,
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
        self.navigator.changed.connect(self._update_nav_buttons)
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
        panel_width = min(InfoPanel.WIDTH, w - 2 * BAR_MARGIN)
        self.info_panel.setGeometry(
            w - panel_width - BAR_MARGIN, BAR_MARGIN, panel_width, bar_top - 2 * BAR_MARGIN
        )
        self.editor.layout(w, h, bar_top, BAR_MARGIN, panel_width)
        self.job_indicator.move(16, 16)
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
            self.info_panel.hide()
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
        if kind is None or kind is MediaKind.AUDIO or self.editor.active:
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
        self.editor.view_changed()
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
        if self.editor.active and not key[1] and not key[2] and not event.isAutoRepeat():
            if self.editor.handle_key(key[0]):
                event.accept()
                return
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
        # Windows' own dialog, told not to add the file to Recent / Quick Access.
        current = self.player.path or self._nav_anchor
        path = get_open_path(int(self.winId()), current.parent if current else None)
        if path is not None:
            self.open_file(path)

    def _escape(self) -> None:
        if self.info_panel.isVisible():
            self.toggle_info()
        else:
            self.exit_fullscreen()

    # -- file operations ------------------------------------------------------------

    def show_in_explorer(self) -> None:
        if self.player.path is not None:
            show_in_explorer(self.player.path)

    def toggle_info(self) -> None:
        if self.info_panel.isVisible():
            self.info_panel.hide()
        elif self.player.path is not None and not self.editor.active:
            self.info_panel.show()
            self.info_panel.raise_()
            self.toast.raise_()
            self._load_info()

    def _load_info(self) -> None:
        path, kind = self.player.path, self.player.kind
        if path is None:
            return
        self.info_panel.show_loading()

        def work() -> None:  # ffprobe can take a moment; keep the UI responsive
            try:
                info: FileInfo | None = read_info(path, kind)
            except OSError:
                info = None
            self._info_bridge.post((path, info))

        threading.Thread(target=work, name="file-info", daemon=True).start()

    def _info_ready(self, results: list[tuple[Path, FileInfo | None]]) -> None:
        for path, info in results:
            if path != self.player.path or not self.info_panel.isVisible():
                continue  # the user moved on
            if info is None:
                self.info_panel.set_info([("Error", "Couldn't read this file.")], [])
            else:
                self.info_panel.set_info(info.basic, info.raw)

    def confirm_delete(self, path: Path) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Delete")
        box.setText(f"Move {path.name} to the Recycle Bin?")
        delete = box.addButton("Move to Recycle Bin", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(delete)
        box.exec()
        return box.clickedButton() is delete

    def delete_current(self) -> None:
        path = self.player.path
        if path is None or self.editor.active:
            return
        if not can_recycle(path):
            self.toast.show_message(
                f"{path.name} is on a drive without a Recycle Bin, so Glancer won't delete it."
            )
            return
        if not self.confirm_delete(path):
            return
        if self.navigator.folder != path.parent:
            self._enter_folder(path.parent)
        target = self.navigator.neighbor(path, 1) or self.navigator.neighbor(path, -1)
        self.player.unload()  # release mpv's handle on the file first
        try:
            move_to_recycle_bin(path)
        except (RecycleError, OSError) as exc:
            self.toast.show_message(str(exc) if isinstance(exc, RecycleError)
                                    else f"{path.name} could not be deleted.")
            self.open_file(path)
            return
        if target is not None:
            self.open_file(target)
        else:
            self.clear()
            self.toast.show_message(f"Moved {path.name} to the Recycle Bin")

    # -- editing ----------------------------------------------------------------------

    def begin_edit(self) -> None:
        if self.player.path is not None and not self.editor.active:
            self.editor.begin()

    def confirm_overwrite(self, path: Path) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Overwrite original")
        box.setText(f"Replace {path.name} with the edited version?")
        box.setInformativeText("The original will be moved to the Recycle Bin.")
        replace = box.addButton("Overwrite", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return box.clickedButton() is replace

    def confirm_close_with_jobs(self) -> bool:
        answer = QMessageBox.question(
            self, "Glancer", "An edit is still being saved. Cancel it and close?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _job_finished(self, job: Job, ok: bool) -> None:
        self.editor.job_finished(job)
        if ok:
            self.toast.show_message(f"Saved {job.label}")
        elif job.error != "Cancelled.":
            self.toast.show_message(f"{job.label} wasn't saved. {job.error}")

    def save_frame(self) -> None:
        path, kind = self.player.path, self.player.kind
        if path is None or kind is None or not kind.has_frames:
            return
        target = frame_filename(path, self.player.frame_number())
        try:
            self.player.save_frame(target)
        except Exception:
            self.toast.show_message("The frame could not be saved.")
            return
        self.toast.show_message(f"Saved {target.name}")

    def open_file(self, path: Path) -> None:
        if self.player.load(path):
            self._nav_anchor = path
            folder = path.parent
            if folder not in self._explorer_queries:
                # Runs alongside mpv's loading; read in _enter_folder.
                self._explorer_queries[folder] = ExplorerSortQuery(folder)

    def clear(self) -> None:
        """Close the current file and return to the empty start screen."""
        self.editor.cancel()
        self.player.unload()
        self._media_size = None
        self._nav_anchor = None
        self.setWindowTitle("Glancer")
        self.bar.set_kind(None)
        self._show_empty_state(True)

    def arrow(self, direction: int) -> None:
        """Left/Right: frames on video, ±5 s on audio, previous/next item on photos."""
        kind = self.player.kind
        if kind is None:
            if self._nav_anchor is not None:  # e.g. after a file failed to open
                self.navigate(direction)
            return
        if kind.has_frames:
            self.player.step(direction)
        elif kind is MediaKind.AUDIO:
            self.player.seek_relative(direction * AUDIO_ARROW_SEEK_SECONDS)
        else:
            self.navigate(direction)

    # -- folder navigation ----------------------------------------------------------

    def navigate(self, direction: int) -> None:
        """Open the previous (-1) or next (+1) media file in the folder."""
        current = self._nav_anchor
        if current is None:
            return
        if self.editor.active:
            self.toast.show_message("Save or cancel the edit first.")
            return
        if self.navigator.folder != current.parent:
            self._enter_folder(current.parent)
        target = self.navigator.neighbor(current, direction)
        if target is None:
            self.toast.show_message("First item" if direction < 0 else "Last item")
        else:
            self.open_file(target)

    def _enter_folder(self, folder: Path) -> None:
        self.navigator.set_folder(folder, self._order_for(folder))

    def _order_for(self, folder: Path) -> SortOrder:
        if folder in self._folder_orders:
            return self._folder_orders[folder]
        query = self._explorer_queries.get(folder)
        explorer = query.result() if query is not None else None
        if explorer is not None:
            self._order_sources[folder] = "Following the open Explorer window"
            order = explorer
        else:
            self._order_sources[folder] = "Default order"
            order = self._settings.default_sort
        self._folder_orders[folder] = order
        return order

    def _choose_sort(self, key: SortKey | None = None, descending: bool | None = None) -> None:
        folder = self.navigator.folder
        if folder is None:
            return
        old = self.navigator.order
        order = SortOrder(
            key if key is not None else old.key,
            descending if descending is not None else old.descending,
        )
        self._folder_orders[folder] = order
        self._order_sources[folder] = "Custom (this folder, until Glancer closes)"
        self.navigator.set_order(order)
        self._update_nav_buttons()

    def _save_default_sort(self) -> None:
        self._settings.default_sort = self.navigator.order
        self.toast.show_message("Saved as the default sort order")

    def _refresh_sort_menu(self) -> None:
        folder = self.navigator.folder
        order = self.navigator.order
        self._sort_source_action.setText(
            self._order_sources.get(folder, "Default order") if folder else "No folder open"
        )
        for key, action in self._sort_key_actions.items():
            action.setChecked(key is order.key)
            action.setEnabled(folder is not None)
        self._ascending_action.setChecked(not order.descending)
        self._descending_action.setChecked(order.descending)

    def _update_nav_buttons(self) -> None:
        current = self._nav_anchor
        has_folder = current is not None and self.navigator.folder == current.parent
        self.bar.previous_button.setEnabled(
            has_folder and self.navigator.neighbor(current, -1) is not None  # type: ignore[arg-type]
        )
        self.bar.next_button.setEnabled(
            has_folder and self.navigator.neighbor(current, 1) is not None  # type: ignore[arg-type]
        )

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
        if self.editor.active and path != self.editor.path:
            self.editor.cancel()
        self.setWindowTitle(f"{path.name} — Glancer")
        if self.navigator.folder != path.parent:
            self._enter_folder(path.parent)
        self._update_nav_buttons()
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
        if self.info_panel.isVisible():
            self._load_info()
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
        if self.jobs.busy:
            if not self.confirm_close_with_jobs():
                event.ignore()
                return
            self.jobs.cancel_all()
        QApplication.instance().removeEventFilter(self)
        self._info_bridge.close()
        self._idle_timer.stop()
        geometry = self._normal_geometry if self.isFullScreen() else self.saveGeometry()
        if geometry is not None:
            self._settings.window_geometry = geometry
        self.view.release()
        self.player.close()
        self._settings.sync()
        event.accept()
        QApplication.quit()
