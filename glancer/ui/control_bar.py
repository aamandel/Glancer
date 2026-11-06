"""The bottom overlay strip: scrub row plus one control row.

    video / animated:  0:12.400 ━━━━━━━━━━●━━━━━━━━━━━━━━━━━━━━ 1:30.000
                       C:\\…\\clip.mp4      ◀|   ▶   |▶      🔊━━ 1× ⟲ ≡ ⛶
    audio:             same, with ⏪/⏩ (±5 s) instead of frame steps
    photo:             C:\\…\\photo.jpg       ‹  ⟳  125%  ›            ≡ ⛶

The control row is a three-column grid with equal stretch on the outer
columns, so the centre controls stay centred whatever the path's width.
The bar only emits signals and shows state; MainWindow decides what a
button means for the current file.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from glancer.media_types import MediaKind
from glancer.player.mpv_player import SPEEDS
from glancer.ui.theme import BAR_BACKGROUND, TEXT, Glyph, accent_color, icon_font
from glancer.utils import format_time

SEEK_STEPS = 100_000
CORNER_RADIUS = 10


def format_speed(speed: float) -> str:
    return f"{speed:g}×"


class ElidedLabel(QLabel):
    """Shows text trimmed in the middle (C:\\Users\\…\\clip.mp4) to fit."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._refresh()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        metrics = QFontMetrics(self.font())
        self.setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideMiddle, self.width()))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())


class SeekSlider(QSlider):
    """A slider that jumps straight to the clicked point and keeps dragging.

    While editing it shows the trim range (markers) and the handle can't leave
    it (limits). Its groove maps value -> x as:

        x = handle_width / 2 + value / maximum * (width - handle_width)
    """

    resized = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.markers: tuple[float | None, float | None] = (None, None)
        self.limits: tuple[int, int] | None = None  # allowed values while editing

    def set_markers(self, start: float | None, end: float | None) -> None:
        """Trim start/end as fractions of the duration (None hides a marker)."""
        if (start, end) != self.markers:
            self.markers = (start, end)
            self.update()

    def set_limits(self, limits: tuple[int, int] | None) -> None:
        self.limits = limits

    def _clamp(self, value: int) -> int:
        if self.limits is None:
            return value
        return max(self.limits[0], min(self.limits[1], value))

    def _handle_width(self) -> int:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        return self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self
        ).width()

    def groove_span(self) -> tuple[float, float]:
        """(x of value 0, pixels from value 0 to the maximum), in slider coordinates."""
        handle = self._handle_width()
        return handle / 2, float(max(1, self.width() - handle))

    def _value_at(self, x: float) -> int:
        left, span = self.groove_span()
        fraction = max(0.0, min(1.0, (x - left) / span))
        return self._clamp(round(self.minimum() + fraction * (self.maximum() - self.minimum())))

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self.resized.emit()

    def paintEvent(self, event: QPaintEvent) -> None:
        start, end = self.markers
        if start is None and end is None:
            super().paintEvent(event)
            return
        # Editing: grey outside the kept range, accent up to the handle, a
        # lighter accent from the handle to the end marker.
        left, span = self.groove_span()
        rng = max(1, self.maximum() - self.minimum())

        def x_of(fraction: float) -> float:
            return left + max(0.0, min(1.0, fraction)) * span

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = accent_color()
        mid = self.height() / 2
        x1 = x_of(start if start is not None else 0.0)
        x2 = x_of(end if end is not None else 1.0)
        xh = x_of((self.value() - self.minimum()) / rng)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 71))  # the groove colour in theme.py
        painter.drawRoundedRect(QRectF(left, mid - 2, span, 4), 2, 2)
        dim = QColor(accent)
        dim.setAlpha(90)
        painter.fillRect(QRectF(x1, mid - 2, max(0.0, x2 - x1), 4), dim)
        painter.fillRect(QRectF(x1, mid - 2, max(0.0, min(xh, x2) - x1), 4), accent)
        painter.setPen(QPen(accent, 2))
        for x in (x1 if start is not None else None, x2 if end is not None else None):
            if x is not None:
                painter.drawLine(QPointF(x, mid - 8), QPointF(x, mid + 8))
        # The handle (the stylesheet style would repaint the whole groove if asked
        # for just the handle). Same look as theme.py: 12 px, accent, white on hover.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(TEXT) if self.underMouse() else accent)
        painter.drawEllipse(QPointF(xh, mid), 6, 6)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            handle = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self
            )
            if not handle.contains(event.position().toPoint()):
                self.setValue(self._value_at(event.position().x()))
        # The handle is now under the cursor, so the base class starts a drag.
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.limits is not None and self.isSliderDown():
            # Position the handle ourselves so it can't be dragged out of the range.
            self.setSliderPosition(self._value_at(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)


def _icon_button(glyph: str, tip: str, parent: QWidget, size: float = 12) -> QToolButton:
    button = QToolButton(parent)
    button.setText(glyph)
    button.setFont(icon_font(size))
    button.setToolTip(tip)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setAutoRaise(True)
    return button


class ControlBar(QWidget):
    back_clicked = Signal()
    forward_clicked = Signal()
    play_clicked = Signal()
    previous_item_clicked = Signal()
    next_item_clicked = Signal()
    rotate_clicked = Signal()
    fullscreen_clicked = Signal()
    mute_clicked = Signal()
    volume_requested = Signal(int)
    speed_requested = Signal(float)
    loop_requested = Signal(bool)
    seek_started = Signal()
    seek_previewed = Signal(float)  # seconds, while dragging
    seek_finished = Signal(float)  # seconds, on release

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ControlBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._kind: MediaKind | None = None
        self._duration = 0.0
        self._scrubbing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 6, 14, 6)
        outer.setSpacing(0)

        # -- scrub row
        self.scrub_row = QWidget(self)
        scrub = QHBoxLayout(self.scrub_row)
        scrub.setContentsMargins(0, 0, 0, 0)
        scrub.setSpacing(10)
        self.time_label = QLabel(format_time(0), self.scrub_row)
        self.duration_label = QLabel(format_time(0), self.scrub_row)
        for label in (self.time_label, self.duration_label):
            label.setObjectName("TimeLabel")
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._fit_time_labels()
        self.seek_slider = SeekSlider(self.scrub_row)
        self.seek_slider.setRange(0, SEEK_STEPS)
        scrub.addWidget(self.time_label)
        scrub.addWidget(self.seek_slider, 1)
        scrub.addWidget(self.duration_label)
        outer.addWidget(self.scrub_row)

        # -- control row: [path | centre | right]
        row = QGridLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setHorizontalSpacing(12)
        outer.addLayout(row)

        self.path_label = ElidedLabel(self)
        row.addWidget(self.path_label, 0, 0)

        self.transport = QWidget(self)
        transport = QHBoxLayout(self.transport)
        transport.setContentsMargins(0, 0, 0, 0)
        transport.setSpacing(6)
        self.back_button = _icon_button(Glyph.FRAME_BACK, "", self.transport)
        self.play_button = _icon_button(Glyph.PLAY, "Play (Space)", self.transport, 16)
        self.forward_button = _icon_button(Glyph.FRAME_FORWARD, "", self.transport)
        for button in (self.back_button, self.play_button, self.forward_button):
            transport.addWidget(button)
        row.addWidget(self.transport, 0, 1, Qt.AlignmentFlag.AlignCenter)

        self.photo_controls = QWidget(self)
        photo = QHBoxLayout(self.photo_controls)
        photo.setContentsMargins(0, 0, 0, 0)
        photo.setSpacing(6)
        self.previous_button = _icon_button(Glyph.PREVIOUS, "Previous item (Shift+←)", self)
        self.rotate_button = _icon_button(Glyph.ROTATE, "Rotate view (R)", self)
        self.zoom_label = QLabel("100%", self)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setMinimumWidth(QFontMetrics(self.font()).horizontalAdvance("1600%") + 8)
        self.next_button = _icon_button(Glyph.NEXT, "Next item (Shift+→)", self)
        for widget in (self.previous_button, self.rotate_button, self.zoom_label, self.next_button):
            photo.addWidget(widget)
        row.addWidget(self.photo_controls, 0, 1, Qt.AlignmentFlag.AlignCenter)

        right = QHBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(2)
        right.addStretch(1)
        self.playback_extras = QWidget(self)
        extras = QHBoxLayout(self.playback_extras)
        extras.setContentsMargins(0, 0, 0, 0)
        extras.setSpacing(2)
        self.mute_button = _icon_button(Glyph.VOLUME, "Mute (M)", self.playback_extras)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal, self.playback_extras)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(76)
        self.volume_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume_slider.setToolTip("Volume (Ctrl+wheel)")
        self.speed_button = QToolButton(self.playback_extras)
        self.speed_button.setText(format_speed(1.0))
        self.speed_button.setToolTip("Speed ([ and ], Backspace resets)")
        self.speed_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.speed_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.speed_menu = QMenu(self.speed_button)
        for speed in SPEEDS:
            action = self.speed_menu.addAction(format_speed(speed))
            action.triggered.connect(lambda _=False, s=speed: self.speed_requested.emit(s))
        self.speed_button.setMenu(self.speed_menu)
        self.loop_button = _icon_button(Glyph.LOOP, "Loop (L)", self.playback_extras)
        self.loop_button.setCheckable(True)
        for widget in (self.mute_button, self.volume_slider, self.speed_button, self.loop_button):
            extras.addWidget(widget)
        right.addWidget(self.playback_extras)
        self.menu_button = _icon_button(Glyph.MENU, "Menu", self)
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.fullscreen_button = _icon_button(Glyph.FULLSCREEN, "Fullscreen (F11)", self)
        right.addWidget(self.menu_button)
        right.addWidget(self.fullscreen_button)
        row.addLayout(right, 0, 2)
        row.setColumnStretch(0, 1)
        row.setColumnStretch(2, 1)

        self.back_button.clicked.connect(self.back_clicked)
        self.forward_button.clicked.connect(self.forward_clicked)
        self.play_button.clicked.connect(self.play_clicked)
        self.previous_button.clicked.connect(self.previous_item_clicked)
        self.next_button.clicked.connect(self.next_item_clicked)
        self.rotate_button.clicked.connect(self.rotate_clicked)
        self.fullscreen_button.clicked.connect(self.fullscreen_clicked)
        self.mute_button.clicked.connect(self.mute_clicked)
        self.loop_button.clicked.connect(self.loop_requested)
        self.volume_slider.valueChanged.connect(self.volume_requested)
        self.seek_slider.sliderPressed.connect(self._seek_pressed)
        self.seek_slider.sliderMoved.connect(self._seek_moved)
        self.seek_slider.sliderReleased.connect(self._seek_released)

        self.previous_button.setEnabled(False)  # MainWindow enables them per file
        self.next_button.setEnabled(False)
        self.set_kind(None)

    # -- mode -----------------------------------------------------------------

    @property
    def kind(self) -> MediaKind | None:
        return self._kind

    @property
    def scrubbing(self) -> bool:
        return self._scrubbing

    def set_kind(self, kind: MediaKind | None) -> None:
        self._kind = kind
        is_photo = kind is MediaKind.IMAGE
        self.scrub_row.setVisible(not is_photo)
        self.transport.setVisible(not is_photo)
        self.photo_controls.setVisible(is_photo)
        self.playback_extras.setVisible(not is_photo)
        if kind is MediaKind.AUDIO:
            self.back_button.setText(Glyph.REWIND)
            self.forward_button.setText(Glyph.FAST_FORWARD)
            self.back_button.setToolTip("Back 5 s (←)")
            self.forward_button.setToolTip("Forward 5 s (→)")
        else:
            self.back_button.setText(Glyph.FRAME_BACK)
            self.forward_button.setText(Glyph.FRAME_FORWARD)
            self.back_button.setToolTip("Previous frame (←)")
            self.forward_button.setToolTip("Next frame (→)")
        self._update_seek_enabled()
        self.adjustSize()

    # -- state from the player --------------------------------------------------

    def set_path(self, text: str) -> None:
        self.path_label.set_full_text(text)

    def set_duration(self, seconds: float) -> None:
        self._duration = max(0.0, seconds)
        self.duration_label.setText(format_time(self._duration))
        self._fit_time_labels()
        self._update_seek_enabled()

    def _fit_time_labels(self) -> None:
        """Fixed widths, sized for the widest digits, so the scrub bar (and the
        editor's trim strip lined up with it) doesn't shift as the time changes."""
        pattern = format_time(self._duration)
        for label in (self.time_label, self.duration_label):
            label.ensurePolished()
            metrics = label.fontMetrics()
            widest = max("0123456789", key=metrics.horizontalAdvance)
            text = "".join(widest if c.isdigit() else c for c in pattern)
            label.setFixedWidth(metrics.horizontalAdvance(text) + 2)

    def set_position(self, seconds: float) -> None:
        if self._scrubbing:
            return
        self.time_label.setText(format_time(seconds))
        value = round(seconds / self._duration * SEEK_STEPS) if self._duration > 0 else 0
        self.seek_slider.blockSignals(True)
        self.seek_slider.setValue(max(0, min(SEEK_STEPS, value)))
        self.seek_slider.blockSignals(False)

    def set_paused(self, paused: bool) -> None:
        self.play_button.setText(Glyph.PLAY if paused else Glyph.PAUSE)
        self.play_button.setToolTip("Play (Space)" if paused else "Pause (Space)")

    def set_volume(self, volume: int) -> None:
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(volume)
        self.volume_slider.blockSignals(False)

    def set_muted(self, muted: bool) -> None:
        self.mute_button.setText(Glyph.MUTED if muted else Glyph.VOLUME)
        self.mute_button.setToolTip("Unmute (M)" if muted else "Mute (M)")

    def set_speed(self, speed: float) -> None:
        self.speed_button.setText(format_speed(speed))

    def set_loop(self, loop: bool) -> None:
        self.loop_button.setChecked(loop)

    def set_fullscreen(self, fullscreen: bool) -> None:
        self.fullscreen_button.setText(Glyph.EXIT_FULLSCREEN if fullscreen else Glyph.FULLSCREEN)
        self.fullscreen_button.setToolTip(
            "Exit fullscreen (F11, Esc)" if fullscreen else "Fullscreen (F11)"
        )

    def set_zoom(self, percent: int) -> None:
        self.zoom_label.setText(f"{percent}%")

    # -- scrubbing --------------------------------------------------------------

    def _update_seek_enabled(self) -> None:
        enabled = self._kind is not None and self._kind.has_timeline and self._duration > 0
        self.seek_slider.setEnabled(enabled)

    def _slider_seconds(self, value: int) -> float:
        return value / SEEK_STEPS * self._duration

    def _seek_pressed(self) -> None:
        self._scrubbing = True
        self.seek_started.emit()
        self._seek_moved(self.seek_slider.value())

    def _seek_moved(self, value: int) -> None:
        seconds = self._slider_seconds(value)
        self.time_label.setText(format_time(seconds))
        self.seek_previewed.emit(seconds)

    def _seek_released(self) -> None:
        self._scrubbing = False
        self.seek_finished.emit(self._slider_seconds(self.seek_slider.value()))

    # -- painting and mouse -------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(BAR_BACKGROUND)
        painter.drawRoundedRect(QRect(QPoint(0, 0), self.size()), CORNER_RADIUS, CORNER_RADIUS)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.accept()  # clicks on the bar never reach the media

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        event.accept()
