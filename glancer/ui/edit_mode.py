"""In-place editor: trim (video, audio), crop (video, photo), save.

    E / ≡ → Edit      pause, reset the view rotation, show the panel (+ crop box)
    trim strip        drag the start / end grips (the video shows the grip's
                      frame), drag inside the range to slide it, click to seek
    I / O             start / end at the current frame
    playback          stays inside the kept range: at its end, Loop on jumps
                      back to the start, Loop off pauses on the last kept frame
    crop box          drag inside to move, drag a handle to resize; presets and
                      X/Y/W/H boxes; coordinates are source pixels, upright
    Save as copy      "name (edited).ext" next to the original, in the background
    Overwrite         confirm, encode to a temp file, then swap (editing/save.py)
    Esc / Cancel      leave without saving

Video and audio encode in the background (editing/jobs.py), so the editor
closes as soon as the job is queued. Photos are cropped immediately.
"""

from __future__ import annotations

import math
import time
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from glancer import zoom
from glancer.editing.commands import (
    Crop,
    EditSpec,
    UnsupportedEdit,
    build_command,
    can_overwrite,
    copy_path,
    output_suffix,
    temp_path,
)
from glancer.editing.jobs import Job, JobRunner
from glancer.editing.save import SaveError, crop_photo, replace_original
from glancer.file_ops import can_recycle
from glancer.media_types import MediaKind
from glancer.metadata import _rotation, probe
from glancer.settings import EditMode
from glancer.ui.theme import BAR_BACKGROUND, DIM_TEXT, TEXT, Glyph, accent_color, icon_font
from glancer.utils import find_binary, format_time

if TYPE_CHECKING:
    from glancer.main_window import MainWindow

HANDLE_PX = 10
MIN_CROP = 16
ASPECTS: list[tuple[str, float | str | None]] = [
    ("Free", None), ("Original", "original"), ("16:9", 16 / 9), ("9:16", 9 / 16),
    ("4:3", 4 / 3), ("1:1", 1.0),
]
VIDEO_MODES = [
    (EditMode.PRECISE, "Precise", "Frame-accurate cut with high-quality H.264 re-encoding"),
    (EditMode.FAST_LOSSLESS, "Fast Lossless",
     "Copies the original video stream. The cut may shift slightly"),
    (EditMode.PRECISE_LOSSLESS, "Precise Lossless",
     "Frame-accurate cut with no quality loss. Produces larger files"),
]
AUDIO_MODES = [
    (EditMode.PRECISE, "Precise", "Exact cut, re-encoded at high quality"),
    (EditMode.FAST_LOSSLESS, "Fast Lossless",
     "Copies the original audio. The cut may shift by up to about 26 ms"),
]
LOSSLESS_AUDIO_NOTE = "Exact cut with no quality loss"
NO_CROP_TIP = "Fast Lossless copies the video stream as-is, so it can't crop."


# -- crop box ----------------------------------------------------------------------


class CropOverlay(QWidget):
    """The crop rectangle over the media, kept in source pixel coordinates."""

    rect_changed = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.to_view: Callable[[float, float], tuple[float, float]] = lambda x, y: (x, y)
        self.to_pixels: Callable[[float, float], tuple[float, float]] = lambda x, y: (x, y)
        self.on_wheel: Callable[[QWheelEvent], None] | None = None
        self.on_pan: Callable[[float, float], None] | None = None  # drag outside the box
        self._pan_last = QPointF()
        self.bounds = (1, 1)
        self.ratio: float | None = None
        self.even = False
        self.crop = QRectF(0, 0, 1, 1)
        self._drag: tuple[int, int] | str | None = None
        self._drag_start = QPointF()
        self._rect_start = QRectF()
        self.hide()

    # -- geometry ----------------------------------------------------------------

    def reset(self, bounds: tuple[int, int], even: bool) -> None:
        self.bounds, self.even, self.ratio = bounds, even, None
        self.crop = QRectF(0, 0, bounds[0], bounds[1])
        self.update()

    def is_full(self) -> bool:
        c = self.current()
        return (c.x, c.y, c.w, c.h) == (0, 0, self.bounds[0], self.bounds[1]) or (
            self.even and c.w >= self.bounds[0] - 1 and c.h >= self.bounds[1] - 1
        )

    def current(self) -> Crop:
        return self._snap(self.crop)

    def set_crop(self, rect: QRectF) -> None:
        self.crop = QRectF(self._snap_rect(rect))
        self.update()
        self.rect_changed.emit()

    def apply_ratio(self, ratio: float | None) -> None:
        """Lock to ratio: the largest such box, centred in the picture."""
        self.ratio = ratio
        if ratio is None:
            return
        width, height = self.bounds
        w = min(width, height * ratio)
        h = w / ratio
        self.set_crop(QRectF((width - w) / 2, (height - h) / 2, w, h))

    def _snap(self, rect: QRectF) -> Crop:
        width, height = self.bounds
        x = max(0, min(width - MIN_CROP, round(rect.x())))
        y = max(0, min(height - MIN_CROP, round(rect.y())))
        w = max(MIN_CROP, min(width - x, round(rect.width())))
        h = max(MIN_CROP, min(height - y, round(rect.height())))
        crop = Crop(x, y, w, h)
        return crop.even() if self.even else crop

    def _snap_rect(self, rect: QRectF) -> QRectF:
        c = self._snap(rect)
        return QRectF(c.x, c.y, c.w, c.h)

    def _view_rect(self) -> QRectF:
        x1, y1 = self.to_view(self.crop.left(), self.crop.top())
        x2, y2 = self.to_view(self.crop.right(), self.crop.bottom())
        return QRectF(QPointF(x1, y1), QPointF(x2, y2))

    def _handles(self) -> dict[tuple[int, int], QPointF]:
        r = self._view_rect()
        xs = {-1: r.left(), 0: r.center().x(), 1: r.right()}
        ys = {-1: r.top(), 0: r.center().y(), 1: r.bottom()}
        return {(hx, hy): QPointF(xs[hx], ys[hy])
                for hx in (-1, 0, 1) for hy in (-1, 0, 1) if (hx, hy) != (0, 0)}

    # -- painting ------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._view_rect()
        shade = QPainterPath()
        shade.addRect(QRectF(self.rect()))
        hole = QPainterPath()
        hole.addRect(r)
        painter.fillPath(shade.subtracted(hole), QColor(0, 0, 0, 140))
        accent = accent_color()
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        for i in (1, 2):  # rule-of-thirds guides
            x = r.left() + r.width() * i / 3
            y = r.top() + r.height() * i / 3
            painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        painter.setPen(QPen(accent, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(r)
        painter.setPen(QPen(accent, 1.5))
        painter.setBrush(QColor(255, 255, 255))
        half = HANDLE_PX / 2
        for point in self._handles().values():
            painter.drawRect(QRectF(point.x() - half, point.y() - half, HANDLE_PX, HANDLE_PX))

    # -- mouse -----------------------------------------------------------------------

    def _hit(self, pos: QPointF) -> tuple[int, int] | str | None:
        for handle, point in self._handles().items():
            if abs(pos.x() - point.x()) <= HANDLE_PX and abs(pos.y() - point.y()) <= HANDLE_PX:
                return handle
        return "move" if self._view_rect().contains(pos) else None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.accept()  # clicks here never pause or pan the media
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag = self._hit(event.position())
        if self._drag is None:
            self._drag = "pan"
            self._pan_last = event.position()
        self._drag_start = QPointF(*self.to_pixels(event.position().x(), event.position().y()))
        self._rect_start = QRectF(self.crop)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag = None
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag is None:
            self.setCursor(self._cursor_for(self._hit(event.position())))
            return
        if self._drag == "pan":
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            if self.on_pan is not None:
                self.on_pan(delta.x(), delta.y())
            return
        p = QPointF(*self.to_pixels(event.position().x(), event.position().y()))
        dx, dy = p.x() - self._drag_start.x(), p.y() - self._drag_start.y()
        if self._drag == "move":
            self.set_crop(self._moved(dx, dy))
        else:
            self.set_crop(self._resized(self._drag, dx, dy))  # type: ignore[arg-type]

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.on_wheel is not None:
            self.on_wheel(event)
        event.accept()

    @staticmethod
    def _cursor_for(hit: tuple[int, int] | str | None) -> Qt.CursorShape:
        if hit == "move":
            return Qt.CursorShape.SizeAllCursor
        if hit is None:
            return Qt.CursorShape.ArrowCursor
        hx, hy = hit  # type: ignore[misc]
        if hx == 0:
            return Qt.CursorShape.SizeVerCursor
        if hy == 0:
            return Qt.CursorShape.SizeHorCursor
        return Qt.CursorShape.SizeFDiagCursor if hx == hy else Qt.CursorShape.SizeBDiagCursor

    def _moved(self, dx: float, dy: float) -> QRectF:
        width, height = self.bounds
        r = QRectF(self._rect_start)
        x = max(0.0, min(width - r.width(), r.x() + dx))
        y = max(0.0, min(height - r.height(), r.y() + dy))
        return QRectF(x, y, r.width(), r.height())

    def _resized(self, handle: tuple[int, int], dx: float, dy: float) -> QRectF:
        hx, hy = handle
        width, height = self.bounds
        r = self._rect_start
        left, right, top, bottom = r.left(), r.right(), r.top(), r.bottom()
        if hx == -1:
            left = max(0.0, min(right - MIN_CROP, left + dx))
        elif hx == 1:
            right = min(float(width), max(left + MIN_CROP, right + dx))
        if hy == -1:
            top = max(0.0, min(bottom - MIN_CROP, top + dy))
        elif hy == 1:
            bottom = min(float(height), max(top + MIN_CROP, bottom + dy))
        if self.ratio is None:
            return QRectF(QPointF(left, top), QPointF(right, bottom))
        return self._locked(hx, hy, left, right, top, bottom)

    def _locked(self, hx: int, hy: int, left: float, right: float, top: float,
                bottom: float) -> QRectF:
        """Keep the ratio, anchored at the opposite edge/corner, inside the picture."""
        ratio = self.ratio or 1.0
        width, height = self.bounds
        ax = right if hx == -1 else left if hx == 1 else (left + right) / 2
        ay = bottom if hy == -1 else top if hy == 1 else (top + bottom) / 2
        w = right - left if hx != 0 else (bottom - top) * ratio
        room_x = ax if hx == -1 else width - ax if hx == 1 else 2 * min(ax, width - ax)
        room_y = ay if hy == -1 else height - ay if hy == 1 else 2 * min(ay, height - ay)
        w = max(MIN_CROP, min(w, room_x, room_y * ratio))
        h = w / ratio
        x = ax - w if hx == -1 else ax if hx == 1 else ax - w / 2
        y = ay - h if hy == -1 else ay if hy == 1 else ay - h / 2
        return QRectF(x, y, w, h)


# -- trim strip ----------------------------------------------------------------------


class TrimStrip(QWidget):
    """The kept range of the timeline, with draggable start / end grips.

        grip drag     -> range_changed + preview_requested(grip time)
        inside drag   -> slide the whole range (length kept), preview its start
        click         -> seek_requested(time), clamped to the range by the controller
    """

    range_changed = Signal(float, float)  # start, end (seconds)
    preview_requested = Signal(float)
    seek_requested = Signal(float)
    drag_finished = Signal()

    HEIGHT = 58
    PAD = 10
    TRACK_H = 24
    GRIP_W = 10

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.duration = 0.0
        self.start = 0.0
        self.end = 0.0
        self.position = 0.0
        self.snap: Callable[[float], float] = lambda t: t
        self.min_length = 0.05  # one frame for video (set by the controller)
        self._drag: str | None = None  # "start", "end", "slide", "pending"
        self._press_x = 0.0
        self._press_range = (0.0, 0.0)
        self.hide()

    # -- geometry --------------------------------------------------------------------

    def _span(self) -> tuple[float, float]:
        left = self.PAD + self.GRIP_W
        return left, max(1.0, self.width() - 2 * left)

    def x_of(self, seconds: float) -> float:
        left, span = self._span()
        return left + (seconds / self.duration if self.duration > 0 else 0.0) * span

    def time_at(self, x: float, clamp: bool = True) -> float:
        left, span = self._span()
        fraction = (x - left) / span
        return (max(0.0, min(1.0, fraction)) if clamp else fraction) * self.duration

    def _track(self) -> QRectF:
        return QRectF(self.PAD, self.PAD, self.width() - 2 * self.PAD, self.TRACK_H)

    def set_range(self, start: float, end: float) -> None:
        self.start, self.end = start, end
        self.update()

    def set_position(self, seconds: float) -> None:
        self.position = seconds
        self.update()


    # -- painting ----------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(BAR_BACKGROUND)
        painter.drawRoundedRect(QRectF(self.rect()), 10, 10)

        track = self._track()
        painter.setBrush(QColor(255, 255, 255, 26))
        painter.drawRoundedRect(track, 4, 4)
        accent = accent_color()
        x1, x2 = self.x_of(self.start), self.x_of(self.end)
        kept = QRectF(x1, track.top(), max(1.0, x2 - x1), track.height())
        fill = QColor(accent)
        fill.setAlpha(70)
        painter.setBrush(fill)
        painter.drawRect(kept)
        painter.setPen(QPen(accent, 2))
        painter.drawLine(QPointF(x1, track.top() + 1), QPointF(x2, track.top() + 1))
        painter.drawLine(QPointF(x1, track.bottom() - 1), QPointF(x2, track.bottom() - 1))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        grips = (QRectF(x1 - self.GRIP_W, track.top(), self.GRIP_W, track.height()),
                 QRectF(x2, track.top(), self.GRIP_W, track.height()))
        for grip in grips:
            painter.drawRoundedRect(grip, 3, 3)
        painter.setPen(QPen(QColor(0, 0, 0, 150), 2))
        for grip in grips:
            cx = grip.center().x()
            painter.drawLine(QPointF(cx, grip.top() + 7), QPointF(cx, grip.bottom() - 7))

        if self.duration > 0:
            px = self.x_of(max(self.start, min(self.end, self.position)))
            painter.setPen(QPen(QColor(TEXT), 2))
            painter.drawLine(QPointF(px, track.top() - 3), QPointF(px, track.bottom() + 3))

        font = QFont(self.font())
        font.setPointSizeF(8.5)
        painter.setFont(font)
        painter.setPen(QColor(DIM_TEXT))
        text_rect = QRectF(self.PAD + 2, track.bottom() + 4, self.width() - 2 * self.PAD - 4, 16)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft, f"Start {format_time(self.start)}")
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter,
                         f"Length {format_time(max(0.0, self.end - self.start))}")
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight, f"End {format_time(self.end)}")

    # -- mouse ---------------------------------------------------------------------------

    def _hit(self, x: float) -> str | None:
        x1, x2 = self.x_of(self.start), self.x_of(self.end)
        if x1 - self.GRIP_W - 4 <= x <= x1 + 3:
            return "start"
        if x2 - 3 <= x <= x2 + self.GRIP_W + 4:
            return "end"
        return "inside" if x1 < x < x2 else None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.accept()
        if event.button() != Qt.MouseButton.LeftButton or self.duration <= 0:
            return
        x = event.position().x()
        hit = self._hit(x)
        self._press_x = x
        self._press_range = (self.start, self.end)
        self._drag = hit if hit in ("start", "end") else "pending"
        if self._drag in ("start", "end"):
            self.preview_requested.emit(self.start if self._drag == "start" else self.end)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x = event.position().x()
        if self._drag is None:
            hit = self._hit(x)
            self.setCursor(Qt.CursorShape.SizeHorCursor if hit in ("start", "end")
                           else Qt.CursorShape.ArrowCursor)
            return
        if self._drag == "pending":
            inside = self._hit(self._press_x) == "inside"
            if inside and abs(x - self._press_x) > 4:
                self._drag = "slide"
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            else:
                return
        start, end = self._press_range
        shift = self.time_at(x, clamp=False) - self.time_at(self._press_x, clamp=False)
        gap = self.min_length
        if self._drag == "start":
            start = max(0.0, min(self.snap(start + shift), end - gap))
            self._emit_range(start, end, start)
        elif self._drag == "end":
            end = min(self.duration, max(self.snap(end + shift), start + gap))
            self._emit_range(start, end, end)
        else:  # slide
            length = end - start
            new_start = self.snap(max(0.0, min(self.duration - length, start + shift)))
            self._emit_range(new_start, min(self.duration, new_start + length), new_start)

    def _emit_range(self, start: float, end: float, preview: float) -> None:
        if (start, end) != (self.start, self.end):
            self.set_range(start, end)
            self.range_changed.emit(start, end)
            self.preview_requested.emit(preview)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        event.accept()
        drag, self._drag = self._drag, None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if drag == "pending":
            self.seek_requested.emit(self.time_at(event.position().x()))
        elif drag is not None:
            self.drag_finished.emit()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        event.accept()

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.accept()


# -- side panel ----------------------------------------------------------------------


class EditPanel(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("EditPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Edit", self)
        title.setObjectName("PanelTitle")
        self.close_button = QToolButton(self)
        self.close_button.setText(Glyph.CLOSE)
        self.close_button.setFont(icon_font(10))
        self.close_button.setToolTip("Cancel (Esc)")
        header.addWidget(title, 1)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        self.mode_box = QComboBox(self)
        self.mode_note = QLabel(self)
        self.mode_note.setWordWrap(True)
        self.mode_note.setObjectName("Note")
        layout.addWidget(self.mode_box)
        layout.addWidget(self.mode_note)

        self.trim = QWidget(self)
        trim = QGridLayout(self.trim)
        trim.setContentsMargins(0, 6, 0, 0)
        trim.addWidget(self._heading("Trim"), 0, 0, 1, 3)
        self.start_label = QLabel(self.trim)
        self.end_label = QLabel(self.trim)
        self.length_label = QLabel(self.trim)
        self.start_button = QPushButton("Set start (I)", self.trim)
        self.end_button = QPushButton("Set end (O)", self.trim)
        self.reset_trim_button = QPushButton("Reset", self.trim)
        trim.addWidget(QLabel("Start", self.trim), 1, 0)
        trim.addWidget(self.start_label, 1, 1)
        trim.addWidget(self.start_button, 1, 2)
        trim.addWidget(QLabel("End", self.trim), 2, 0)
        trim.addWidget(self.end_label, 2, 1)
        trim.addWidget(self.end_button, 2, 2)
        trim.addWidget(QLabel("Length", self.trim), 3, 0)
        trim.addWidget(self.length_label, 3, 1)
        trim.addWidget(self.reset_trim_button, 3, 2)
        layout.addWidget(self.trim)

        self.crop = QWidget(self)
        crop = QGridLayout(self.crop)
        crop.setContentsMargins(0, 6, 0, 0)
        crop.addWidget(self._heading("Crop"), 0, 0, 1, 4)
        self.aspect_box = QComboBox(self.crop)
        for label, _ in ASPECTS:
            self.aspect_box.addItem(label)
        self.reset_crop_button = QPushButton("Reset", self.crop)
        crop.addWidget(self.aspect_box, 1, 0, 1, 3)
        crop.addWidget(self.reset_crop_button, 1, 3)
        self.spins: dict[str, QSpinBox] = {}
        for i, name in enumerate("XYWH"):
            spin = QSpinBox(self.crop)
            spin.setRange(0, 100_000)
            spin.setKeyboardTracking(False)
            self.spins[name] = spin
            crop.addWidget(QLabel(name, self.crop), 2 + i // 2, (i % 2) * 2)
            crop.addWidget(spin, 2 + i // 2, (i % 2) * 2 + 1)
        layout.addWidget(self.crop)
        layout.addStretch(1)

        self.copy_button = QPushButton("Save as copy", self)
        self.copy_button.setObjectName("Primary")
        self.overwrite_button = QPushButton("Overwrite original", self)
        self.cancel_button = QPushButton("Cancel", self)
        for button in (self.copy_button, self.overwrite_button, self.cancel_button):
            layout.addWidget(button)
        for widget in self.findChildren(QWidget):
            if isinstance(widget, (QPushButton, QToolButton, QComboBox)):
                widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def _heading(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("Heading")
        return label

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        event.accept()

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        event.accept()


# -- controller --------------------------------------------------------------------------


class EditController:
    """Owns the panel and crop box; turns choices into saved files."""

    def __init__(self, window: "MainWindow"):
        self.w = window
        self.panel = EditPanel(window.media_area)
        self.overlay = CropOverlay(window.media_area)
        self.overlay.on_wheel = window._wheel_zoom
        self.overlay.on_pan = window._dragged
        self.overlay.to_view = self._to_view
        self.overlay.to_pixels = self._to_pixels
        self.strip = TrimStrip(window.media_area)
        self.strip.snap = self._snap_time
        self.strip.range_changed.connect(self._strip_range)
        self.strip.preview_requested.connect(self._preview)
        self.strip.seek_requested.connect(lambda t: self._seek(self.clamp(t)))
        self.strip.drag_finished.connect(self._preview_flush)
        self._preview_timer = QTimer(window)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(50)
        self._preview_timer.timeout.connect(self._preview_flush)
        self._preview_target: float | None = None
        # After our own seeks, mpv keeps reporting the old position for a moment.
        # Until it reports the target (or 1 s passes), the dot and the strip's
        # playhead show the target instead.
        self._seek_target: float | None = None
        self._seek_deadline = 0.0
        self._strip_box = (0, 0)  # bar_top, margin from the last layout
        window.bar.seek_slider.resized.connect(self._place_strip)
        window.bar.seek_previewed.connect(self._scrub_previewed)
        self.active = False
        self.path: Path | None = None
        self.kind: MediaKind | None = None
        self.start: float | None = None
        self.end: float | None = None
        self.frame_time = 1 / 30
        self.audio_codec: str | None = None
        self.pixel_size = (1, 1)
        self.crop_allowed = False
        self._reserved: set[Path] = set()  # copy names already given to queued jobs

        p = self.panel
        p.close_button.clicked.connect(self.cancel)
        p.cancel_button.clicked.connect(self.cancel)
        p.start_button.clicked.connect(self.set_start)
        p.end_button.clicked.connect(self.set_end)
        p.reset_trim_button.clicked.connect(self.reset_trim)
        p.reset_crop_button.clicked.connect(self.reset_crop)
        p.aspect_box.currentIndexChanged.connect(self._aspect_chosen)
        p.mode_box.currentIndexChanged.connect(self._mode_changed)
        p.copy_button.clicked.connect(lambda: self.save(overwrite=False))
        p.overwrite_button.clicked.connect(lambda: self.save(overwrite=True))
        for spin in p.spins.values():
            spin.valueChanged.connect(self._spins_changed)
        self.overlay.rect_changed.connect(self._crop_changed)
        # The duration can arrive just after editing starts.
        window.player.duration_changed.connect(lambda _d: self.active and self._update_trim())
        window.player.position_changed.connect(self._position_changed)
        window.player.loop_changed.connect(lambda _loop: self._apply_bounds())

    # -- coordinates: source pixels <-> view -------------------------------------------

    def _display_scale(self) -> tuple[float, float, tuple[float, float]]:
        display = self.w._media_size or (float(self.pixel_size[0]), float(self.pixel_size[1]))
        return display[0] / self.pixel_size[0], display[1] / self.pixel_size[1], display

    def _to_view(self, x: float, y: float) -> tuple[float, float]:
        sx, sy, display = self._display_scale()
        return zoom.media_to_view(self.w.zoom_state, self.w._view_size(), display, (x * sx, y * sy))

    def _to_pixels(self, x: float, y: float) -> tuple[float, float]:
        sx, sy, display = self._display_scale()
        mx, my = zoom.view_to_media(self.w.zoom_state, self.w._view_size(), display, (x, y))
        return mx / sx, my / sy

    # -- start / stop ----------------------------------------------------------------------

    @staticmethod
    def can_edit(kind: MediaKind | None) -> bool:
        return kind in (MediaKind.VIDEO, MediaKind.AUDIO, MediaKind.IMAGE)

    def begin(self) -> None:
        player = self.w.player
        path, kind = player.path, player.kind
        if path is None:
            return
        if not self.can_edit(kind):
            self.w.toast.show_message("Animated images can't be edited.")
            return
        player.set_paused(True)
        player.set_rotation(0)  # edits apply to the file as stored, upright
        self.path, self.kind = path, kind
        self.start = self.end = None
        self._read_source(path, kind)  # type: ignore[arg-type]

        p = self.panel
        p.trim.setVisible(kind is not MediaKind.IMAGE)
        p.crop.setVisible(kind is not MediaKind.AUDIO)
        p.mode_box.blockSignals(True)
        p.mode_box.clear()
        modes = VIDEO_MODES if kind is MediaKind.VIDEO else AUDIO_MODES
        lossless_audio = kind is MediaKind.AUDIO and self._lossless_audio()
        show_modes = kind is not MediaKind.IMAGE and not lossless_audio
        if show_modes:
            for mode, label, note in modes:
                p.mode_box.addItem(label, mode)
                p.mode_box.setItemData(p.mode_box.count() - 1, note, Qt.ItemDataRole.ToolTipRole)
            wanted = self.w._settings.default_edit_mode
            index = max(0, p.mode_box.findData(wanted))
            p.mode_box.setCurrentIndex(index)
        p.mode_box.blockSignals(False)
        p.mode_box.setVisible(show_modes)
        p.mode_note.setText(LOSSLESS_AUDIO_NOTE if lossless_audio else "")
        p.mode_note.setVisible(lossless_audio or show_modes)

        if kind is not MediaKind.AUDIO:
            self.overlay.reset(self.pixel_size, even=kind is MediaKind.VIDEO)
            p.aspect_box.blockSignals(True)
            p.aspect_box.setCurrentIndex(0)
            p.aspect_box.blockSignals(False)
            for name, spin in p.spins.items():
                spin.blockSignals(True)
                spin.setMaximum(self.pixel_size[0] if name in "XW" else self.pixel_size[1])
                spin.blockSignals(False)
            self._crop_changed()
        self.active = True
        self.w.info_panel.hide()
        p.show()
        if kind is not MediaKind.IMAGE:
            self.strip.min_length = self.frame_time if kind is MediaKind.VIDEO else 0.05
            self.strip.show()
        self._mode_changed()  # also fixes the stacking order
        self._update_trim()
        self._apply_bounds()
        self.w._layout_overlays()
        self.w.user_activity()

    def cancel(self) -> None:
        if not self.active:
            return
        self.active = False
        self.panel.hide()
        self.overlay.hide()
        self.strip.hide()
        self._preview_timer.stop()
        self._apply_bounds()
        self.w.bar.seek_slider.set_markers(None, None)
        self.w.bar.seek_slider.set_limits(None)
        self.w._layout_overlays()
        self.w.user_activity()

    def _read_source(self, path: Path, kind: MediaKind) -> None:
        self.audio_codec = None
        if kind is MediaKind.IMAGE:
            from PIL import Image

            with Image.open(path) as image:
                width, height = image.size
                if image.getexif().get(0x0112, 1) in (5, 6, 7, 8):  # stored sideways
                    width, height = height, width
            self.pixel_size = (width, height)
            return
        data = probe(path)
        streams = data.get("streams", [])
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        self.audio_codec = audio.get("codec_name") if audio else None
        video = next((s for s in streams if s.get("codec_type") == "video"
                      and not s.get("disposition", {}).get("attached_pic")), None)
        if video:
            width, height = int(video.get("width", 1)), int(video.get("height", 1))
            if _rotation(video) % 180:
                width, height = height, width
            self.pixel_size = (width, height)
            try:
                fps = float(Fraction(video.get("avg_frame_rate") or "30/1"))
            except (ValueError, ZeroDivisionError):
                fps = 30.0
            self.frame_time = 1 / fps if fps > 0 else 1 / 30

    def _lossless_audio(self) -> bool:
        return EditSpec(Path("x"), MediaKind.AUDIO, EditMode.PRECISE,
                        audio_codec=self.audio_codec).is_lossless_audio

    def _mode(self) -> EditMode:
        data = self.panel.mode_box.currentData()
        return data if isinstance(data, EditMode) else EditMode.PRECISE

    def _mode_changed(self) -> None:
        p = self.panel
        if not p.mode_box.isHidden():
            p.mode_note.setText(
                p.mode_box.itemData(p.mode_box.currentIndex(), Qt.ItemDataRole.ToolTipRole) or ""
            )
        crop_allowed = self.kind is MediaKind.IMAGE or (
            self.kind is MediaKind.VIDEO and self._mode() is not EditMode.FAST_LOSSLESS
        )
        self.crop_allowed = crop_allowed
        p.crop.setEnabled(crop_allowed)
        p.crop.setToolTip("" if crop_allowed else NO_CROP_TIP)
        show_box = self.active and self.kind is not MediaKind.AUDIO and crop_allowed
        self.overlay.setVisible(show_box)
        self._restack()
        spec = self._spec()
        overwrite_ok = spec is None or can_overwrite(spec)
        p.overwrite_button.setEnabled(overwrite_ok)
        p.overwrite_button.setToolTip(
            "" if overwrite_ok else
            "Precise Lossless makes a different, larger file, so it only saves copies."
            if self._mode() is EditMode.PRECISE_LOSSLESS
            else "This edit changes the file type, so it only saves copies."
        )

    def _restack(self) -> None:
        """Bottom to top: crop box (covers the whole media area), controls, trim
        strip, panel, then the job card and toasts. Anything below the crop box
        can't be clicked."""
        for widget in (self.overlay, self.w.bar, self.strip, self.panel,
                       self.w.job_indicator, self.w.toast):
            widget.raise_()

    # -- trim -------------------------------------------------------------------------------

    def set_start(self) -> None:
        position = self.w.player.position
        if self.end is not None and position >= self.end:
            self.w.toast.show_message("The start must be before the end.")
            return
        self.start = position
        self._update_trim()

    def set_end(self) -> None:
        position = self.w.player.position
        if self.start is not None and position <= self.start:
            self.w.toast.show_message("The end must be after the start.")
            return
        self.end = position
        self._update_trim()

    def reset_trim(self) -> None:
        self.start = self.end = None
        self._update_trim()

    def _snap_time(self, seconds: float) -> float:
        """Video grips land on frames; audio is continuous."""
        if self.kind is not MediaKind.VIDEO or self.frame_time <= 0:
            return seconds
        return round(seconds / self.frame_time) * self.frame_time

    def bounds(self) -> tuple[float, float]:
        """The kept range in seconds (end = the last kept frame / sample time)."""
        duration = self.w.player.duration
        return self.start or 0.0, self.end if self.end is not None else duration

    def clamp(self, seconds: float) -> float:
        if not self.active or self.kind is MediaKind.IMAGE:
            return seconds
        start, end = self.bounds()
        return max(start, min(end, seconds)) if end > start else seconds

    def _strip_range(self, start: float, end: float) -> None:
        duration = self.w.player.duration
        self.start = start if start > 1e-6 else None
        self.end = end if end < duration - 1e-6 else None
        self._update_trim()
        self._apply_bounds()

    def _preview(self, seconds: float) -> None:
        """Show the grip's frame, at most every 50 ms while dragging; the dot and
        the strip's playhead follow the grip at once."""
        self._show(seconds)
        self._preview_target = seconds
        if not self._preview_timer.isActive():
            self._preview_flush()
            self._preview_timer.start()

    def _preview_flush(self) -> None:
        if self._preview_target is not None:
            self.w.player.set_paused(True)
            self._seek(self._preview_target)
            self._preview_target = None

    def _seek(self, seconds: float) -> None:
        self._seek_target = seconds
        self._seek_deadline = time.monotonic() + 1.0
        self._show(seconds)
        self.w.player.seek(seconds)

    def _show(self, seconds: float) -> None:
        """Put the timeline dot and the strip's playhead at the same time."""
        self.strip.set_position(seconds)
        self.w.bar.set_position(seconds)

    def holds_display(self, position: float) -> bool:
        """True while a reported position is stale (we are dragging a grip, or
        mpv hasn't reached our last seek yet)."""
        if not self.active or self.kind is MediaKind.IMAGE:
            return False
        if self.strip._drag is not None or self._preview_target is not None:
            return True
        if self._seek_target is not None:
            if abs(position - self._seek_target) <= self.frame_time and                     time.monotonic() < self._seek_deadline:
                self._seek_target = None  # arrived
            elif time.monotonic() < self._seek_deadline:
                return True
            else:
                self._seek_target = None
        return False

    def _apply_bounds(self) -> None:
        """Let mpv stop or loop exactly at the end of the kept range.

        Measured on mpv 0.42: `end` pauses on the last frame before it, so
        end = last kept frame + half a frame; the A-B loop wraps before any
        frame that ends after B, so B = last kept frame + 1.5 frames.
        """
        player = self.w.player
        bounded = self.active and self.kind is not MediaKind.IMAGE and self.end is not None
        frame = self.frame_time if self.kind is MediaKind.VIDEO else 0.0
        start, end = self.bounds()
        if bounded and player.loop:
            player.set_limits(end=None, loop=(start, end + 1.5 * frame))
        elif bounded:
            player.set_limits(end=end + 0.5 * frame, loop=None)
        else:
            player.set_limits(end=None, loop=None)

    def at_range_end(self) -> bool:
        if not self.active or self.kind is MediaKind.IMAGE:
            return False
        _start, end = self.bounds()
        return self.w.player.position >= end - self.frame_time / 2

    def _position_changed(self, position: float) -> None:
        """Keep playback inside the kept range (mpv itself stops / loops at the end)."""
        if not self.active or self.kind is MediaKind.IMAGE:
            return
        if self.w.bar.scrubbing or self.holds_display(position):
            return  # the dot and strip show the target meanwhile
        self.strip.set_position(position)
        start, end = self.bounds()
        slack = self.frame_time if self.kind is MediaKind.VIDEO else 0.02
        player = self.w.player
        if self.end is not None and position > end + slack:  # e.g. a step past the end
            player.set_paused(True)
            self._seek(end)
        elif self.start is not None and position < start - slack:
            self._seek(start)

    def _scrub_previewed(self, seconds: float) -> None:
        if self.active:
            self.strip.set_position(self.clamp(seconds))

    def _update_trim(self) -> None:
        duration = self.w.player.duration
        start = self.start or 0.0
        end = self.end if self.end is not None else duration
        if self.active and self.kind is not MediaKind.IMAGE:
            self.strip.duration = duration
            self.strip.set_range(start, end)
        p = self.panel
        p.start_label.setText(format_time(start))
        p.end_label.setText(format_time(end) if self.end is not None else "End of file")
        p.length_label.setText(format_time(max(0.0, end - start)))
        slider = self.w.bar.seek_slider
        if duration > 0 and self.active and self.kind is not MediaKind.IMAGE:
            slider.set_markers(start / duration, end / duration)
            steps = slider.maximum()
            slider.set_limits((math.ceil(start / duration * steps),
                               math.floor(end / duration * steps)))

    def _exclusive_end(self) -> float | None:
        if self.end is None:
            return None
        # Keep the frame shown at "end" but not the next one.
        return self.end + (self.frame_time / 2 if self.kind is MediaKind.VIDEO else 0.0)

    # -- crop ------------------------------------------------------------------------------

    def reset_crop(self) -> None:
        self.panel.aspect_box.setCurrentIndex(0)
        self.overlay.reset(self.pixel_size, self.overlay.even)
        self._crop_changed()

    def _aspect_chosen(self, index: int) -> None:
        value = ASPECTS[index][1]
        if value == "original":
            value = self.pixel_size[0] / self.pixel_size[1]
        self.overlay.apply_ratio(value if isinstance(value, float) else None)

    def _crop_changed(self) -> None:
        crop = self.overlay.current()
        for name, value in zip("XYWH", (crop.x, crop.y, crop.w, crop.h)):
            spin = self.panel.spins[name]
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _spins_changed(self) -> None:
        s = self.panel.spins
        rect = QRectF(s["X"].value(), s["Y"].value(), s["W"].value(), s["H"].value())
        ratio = self.overlay.ratio
        if ratio:
            rect.setHeight(rect.width() / ratio)
        self.overlay.set_crop(rect)

    def set_crop(self, crop: Crop) -> None:
        """Programmatic crop (also used by tests)."""
        self.overlay.set_crop(QRectF(crop.x, crop.y, crop.w, crop.h))

    # -- saving -------------------------------------------------------------------------------

    def _spec(self) -> EditSpec | None:
        if self.path is None or self.kind is None or self.kind is MediaKind.IMAGE:
            return None
        crop = None
        if self.kind is MediaKind.VIDEO and self.crop_allowed and not self.overlay.is_full():
            crop = self.overlay.current()
        return EditSpec(self.path, self.kind, self._mode(), self.start, self._exclusive_end(),
                        crop, self.audio_codec)

    def _has_changes(self) -> bool:
        if self.kind is MediaKind.IMAGE:
            return not self.overlay.is_full()
        spec = self._spec()
        return spec is not None and (spec.start is not None or spec.end is not None
                                     or spec.crop is not None)

    def _copy_target(self, suffix: str) -> Path:
        assert self.path is not None
        n = 1
        while True:
            label = "edited" if n == 1 else f"edited {n}"
            target = self.path.with_name(f"{self.path.stem} ({label}){suffix}")
            if not target.exists() and target not in self._reserved:
                return target
            n += 1

    def save(self, overwrite: bool) -> None:
        if not self.active or self.path is None or self.kind is None:
            return
        if not self._has_changes():
            self.w.toast.show_message("Nothing to save: set a trim or a crop first.")
            return
        path, kind = self.path, self.kind
        if overwrite:
            if not can_recycle(path):
                self.w.toast.show_message(
                    f"{path.name} is on a drive without a Recycle Bin, so it can't be overwritten."
                )
                return
            if not self.w.confirm_overwrite(path):
                return
        if kind is MediaKind.IMAGE:
            self._save_photo(path, overwrite)
        else:
            self._queue_encode(path, kind, overwrite)

    def _save_photo(self, path: Path, overwrite: bool) -> None:
        crop = self.overlay.current()
        self.cancel()
        try:
            if overwrite:
                temp = temp_path(path)
                try:
                    crop_photo(path, crop, temp)
                    self.w.player.unload()
                    replace_original(path, temp, MediaKind.IMAGE)
                finally:
                    temp.unlink(missing_ok=True)
                self.w.open_file(path)
                self.w.toast.show_message(f"Saved {path.name}")
            else:
                target = copy_path(path)
                crop_photo(path, crop, target)
                self.w.toast.show_message(f"Saved {target.name}")
        except (SaveError, OSError) as exc:
            if self.w.player.path is None and path.exists():
                self.w.open_file(path)
            self.w.toast.show_message(str(exc) if isinstance(exc, SaveError)
                                      else f"The photo could not be saved: {exc}")

    def _queue_encode(self, path: Path, kind: MediaKind, overwrite: bool) -> None:
        spec = self._spec()
        ffmpeg = find_binary("ffmpeg.exe")
        if spec is None or ffmpeg is None:
            self.w.toast.show_message("ffmpeg is missing, so edits can't be saved.")
            return
        suffix = output_suffix(spec)
        output = temp_path(path, suffix) if overwrite else self._copy_target(suffix)
        try:
            command = build_command(ffmpeg, spec, output)
        except UnsupportedEdit as exc:
            self.w.toast.show_message(str(exc))
            return
        end = spec.end if spec.end is not None else self.w.player.duration
        duration = max(0.001, end - (spec.start or 0.0))
        job = Job(command, output, duration, path.name if overwrite else output.name)
        if overwrite:
            job.on_success = lambda: self._swap(path, output, kind)
        else:
            self._reserved.add(output)
        self.w._settings.default_edit_mode = spec.mode
        self.cancel()
        self.w.job_indicator.submitted()
        self.w.jobs.submit(job)

    def _swap(self, original: Path, temp: Path, kind: MediaKind) -> None:
        """Runs when an overwrite encode finishes (on the main thread)."""
        shown = self.w.player.path == original
        if shown:
            self.w.player.unload()
        try:
            replace_original(original, temp, kind)
        finally:
            if shown and original.exists():
                self.w.open_file(original)

    def job_finished(self, job: Job) -> None:
        self._reserved.discard(job.output)

    # -- keys and layout -------------------------------------------------------------------

    def handle_key(self, key: int) -> bool:
        if key == Qt.Key.Key_I and self.kind is not MediaKind.IMAGE:
            self.set_start()
        elif key == Qt.Key.Key_O and self.kind is not MediaKind.IMAGE:
            self.set_end()
        elif key == Qt.Key.Key_Escape:
            self.cancel()
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_E, Qt.Key.Key_H):
            pass  # no deleting, re-entering or hiding the controls while editing
        else:
            return False
        return True

    def layout(self, width: int, height: int, bar_top: int, margin: int, panel_width: int) -> None:
        self.overlay.setGeometry(0, 0, width, height)
        self._strip_box = (bar_top, margin)
        bottom = bar_top
        if not self.strip.isHidden():
            self._place_strip()
            bottom = self.strip.y()
        self.panel.setGeometry(width - panel_width - margin, margin, panel_width,
                               bottom - 2 * margin)
        if self.active:
            self._update_trim()

    def _place_strip(self) -> None:
        """Line the strip's track up with the scrub bar's groove, just above the bar."""
        if self.strip.isHidden():
            return
        bar_top, margin = self._strip_box
        bar, slider = self.w.bar, self.w.bar.seek_slider
        if bar.layout() is not None:
            bar.layout().activate()
        left, span = slider.groove_span()
        origin = slider.mapTo(self.w.media_area, QPoint(0, 0)).x()
        inset = TrimStrip.PAD + TrimStrip.GRIP_W
        self.strip.setGeometry(round(origin + left - inset), bar_top - TrimStrip.HEIGHT - margin // 2,
                               round(span + 2 * inset), TrimStrip.HEIGHT)

    def view_changed(self) -> None:
        if not self.overlay.isHidden():
            self.overlay.update()



# -- background job indicator ------------------------------------------------------------


class JobIndicator(QWidget):
    """Top-left card: what is being saved, progress, Cancel, and the queue length."""

    def __init__(self, parent: QWidget, runner: JobRunner):
        super().__init__(parent)
        self.setObjectName("JobIndicator")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.runner = runner
        self.queued = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(6)
        row = QHBoxLayout()
        self.label = QLabel(self)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cancel_button.clicked.connect(runner.cancel_current)
        row.addWidget(self.label, 1)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)
        self.setFixedWidth(320)
        runner.started.connect(self._started)
        runner.progress.connect(self._progress)
        runner.finished.connect(self._finished)
        self.hide()

    def submitted(self) -> None:
        self.queued += 1
        self._refresh()

    def _started(self, job: Job) -> None:
        self.queued = max(0, self.queued - 1)
        self.bar.setValue(0)
        self._percent = 0
        self._refresh()
        self.show()
        self.raise_()

    def _progress(self, job: Job, fraction: float) -> None:
        self.bar.setValue(round(fraction * 1000))
        self._percent = round(fraction * 100)
        self._refresh()

    def _finished(self, job: Job, ok: bool) -> None:
        # The next job (if any) starts right after this, inside the runner.
        QTimer.singleShot(0, lambda: self.setVisible(self.runner.current is not None))

    def _refresh(self) -> None:
        job = self.runner.current
        if job is None:
            return
        text = f"Saving {job.label} — {getattr(self, '_percent', 0)}%"
        if self.queued:
            text += f"  (+{self.queued} queued)"
        self.label.setText(text)
        self.adjustSize()
