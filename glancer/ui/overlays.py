"""Click handling on the media plus the smaller overlay widgets.

ClickClassifier turns raw mouse events on the media into clicks and
double-clicks:

    press   -> remember position
    move    -> more than 4 px with the button down = a drag (Phase 3: pan)
    release -> not a drag: wait the system double-click interval, then emit clicked
    double  -> cancel the pending click, emit double_clicked

so a double-click never also pauses and resumes playback.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from glancer.ui.theme import DIM_TEXT, Glyph, TEXT, icon_font

CLICK_SLOP_PX = 4
TOAST_MS = 3500


class ClickClassifier(QObject):
    clicked = Signal()
    double_clicked = Signal()
    dragged = Signal(float, float)  # movement in pixels since the last drag event
    drag_changed = Signal(bool)  # a drag started (True) or ended (False)

    def __init__(self, target: QWidget):
        super().__init__(target)
        self._press: QPointF | None = None
        self._last: QPointF | None = None
        self._dragging = False
        self._swallow_release = False
        # None = the system double-click interval (tests use a shorter one).
        self.interval_ms: int | None = None
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.timeout.connect(self.clicked)
        target.installEventFilter(self)

    @property
    def dragging(self) -> bool:
        return self._dragging

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind not in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        ):
            return False
        assert isinstance(event, QMouseEvent)
        if kind == QEvent.Type.MouseMove:
            if self._press is None or not (event.buttons() & Qt.MouseButton.LeftButton):
                return False
            if not self._dragging:
                delta = event.position() - self._press
                if abs(delta.x()) > CLICK_SLOP_PX or abs(delta.y()) > CLICK_SLOP_PX:
                    self._dragging = True
                    self._last = self._press
                    self.drag_changed.emit(True)
            if self._dragging and self._last is not None:
                step = event.position() - self._last
                self._last = event.position()
                self.dragged.emit(step.x(), step.y())
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if kind == QEvent.Type.MouseButtonPress:
            self._press = event.position()
            self._dragging = False
        elif kind == QEvent.Type.MouseButtonDblClick:
            self._pending.stop()
            self._swallow_release = True
            self.double_clicked.emit()
        elif kind == QEvent.Type.MouseButtonRelease:
            was_click = self._press is not None and not self._dragging
            was_dragging = self._dragging
            self._press = None
            self._last = None
            self._dragging = False
            if was_dragging:
                self.drag_changed.emit(False)
            if self._swallow_release:
                self._swallow_release = False
            elif was_click:
                interval = self.interval_ms
                self._pending.start(interval if interval is not None else QApplication.doubleClickInterval())
        return False


class ZoomBadge(QLabel):
    """"150%" in the top-right corner while zoomed in."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("ZoomBadge")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.hide()

    def set_percent(self, percent: int) -> None:
        self.setText(f"{percent}%")
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            self.move(parent.width() - self.width() - 16, 16)


class Toast(QLabel):
    """A short message near the top of the media that fades away by itself."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setWordWrap(False)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str) -> None:
        self.setText(text)
        self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(TOAST_MS)
        parent = self.parentWidget()
        if parent is not None:
            self.move((parent.width() - self.width()) // 2, 24)


class EmptyState(QWidget):
    open_clicked = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("EmptyState")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.open_button = QPushButton("Open file", self)
        self.open_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        hint = QLabel("or press Ctrl+O", self)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint, 0, Qt.AlignmentFlag.AlignCenter)
        self.open_button.clicked.connect(self.open_clicked)
        self.adjustSize()


class AudioInfo(QWidget):
    """Music icon (when there is no cover art) and "Title — Artist" for audio.

    Cover art itself is drawn by mpv inside the margins MainWindow reserves,
    so this widget only paints the fallback icon and the caption.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("AudioInfo")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.icon = QLabel(Glyph.MUSIC, self)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setStyleSheet(f"color: {DIM_TEXT};")
        self.caption = QLabel(self)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.caption.font()
        font.setPointSizeF(13)
        self.caption.setFont(font)
        self.caption.setStyleSheet(f"color: {TEXT};")
        self._art_height = 0
        self.has_art = False

    @staticmethod
    def caption_for(tags: dict[str, str], fallback: str) -> str:
        title = tags.get("title", "").strip()
        artist = (tags.get("artist") or tags.get("album_artist") or "").strip()
        if title and artist:
            return f"{title} — {artist}"
        return title or fallback

    def set_info(self, caption: str, has_art: bool) -> None:
        self.caption.setText(caption)
        self.has_art = has_art
        self.icon.setVisible(not has_art)

    def layout_in(self, art_top: int, art_bottom: int, width: int) -> None:
        """Place the icon in the art area and the caption just below it."""
        caption_height = self.caption.sizeHint().height()
        self.setGeometry(0, art_top, width, art_bottom - art_top + 8 + caption_height)
        art_height = art_bottom - art_top
        if art_height != self._art_height:
            self._art_height = art_height
            self.icon.setFont(icon_font(max(24, art_height * 0.32)))
        self.icon.setGeometry(0, 0, width, art_height)
        self.caption.setGeometry(16, art_height + 8, width - 32, caption_height)
