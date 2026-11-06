"""Zoom and pan math (pure functions, no Qt or mpv).

Zoom is relative to "fit": 1.0 (100%) means the whole picture fits the view.
Pan is stored the way mpv takes it, as a fraction of the *shown* picture size,
so the same state stays meaningful when the window or the file changes:

    fit       = min(view_w / media_w, view_h / media_h)
    shown_w   = media_w * fit * zoom                        (same for h)
    max_pan_x = max(0, (shown_w - view_w) / 2) / shown_w    # picture edge meets view edge
    mpv:        video-zoom = log2(zoom), video-pan-x = pan_x, video-pan-y = pan_y

Positive pan moves the picture right/down. Media sizes are the displayed
size (already swapped for 90°/270° rotation by the player).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

MIN_ZOOM = 1.0
MAX_ZOOM = 16.0
WHEEL_FACTOR = 1.25  # per wheel notch

Size = tuple[float, float]
Point = tuple[float, float]


@dataclass(frozen=True)
class ZoomState:
    zoom: float = 1.0
    pan_x: float = 0.0  # fraction of the shown width
    pan_y: float = 0.0  # fraction of the shown height

    @property
    def percent(self) -> int:
        return round(self.zoom * 100)

    @property
    def is_zoomed(self) -> bool:
        return self.zoom > MIN_ZOOM + 1e-9


def _valid(view: Size, media: Size) -> bool:
    return min(view) > 0 and min(media) > 0


def shown_size(view: Size, media: Size, zoom: float) -> Size:
    fit = min(view[0] / media[0], view[1] / media[1])
    return media[0] * fit * zoom, media[1] * fit * zoom


def max_pan(view: Size, media: Size, zoom: float) -> Size:
    """Largest allowed |pan| on each axis, as a fraction of the shown size."""
    shown_w, shown_h = shown_size(view, media, zoom)
    return (
        max(0.0, (shown_w - view[0]) / 2) / shown_w,
        max(0.0, (shown_h - view[1]) / 2) / shown_h,
    )


def clamp(state: ZoomState, view: Size, media: Size) -> ZoomState:
    zoom = min(MAX_ZOOM, max(MIN_ZOOM, state.zoom))
    if not _valid(view, media):
        return replace(state, zoom=zoom)
    limit_x, limit_y = max_pan(view, media, zoom)
    return ZoomState(
        zoom,
        max(-limit_x, min(limit_x, state.pan_x)),
        max(-limit_y, min(limit_y, state.pan_y)),
    )


def zoom_at(state: ZoomState, view: Size, media: Size, factor: float, anchor: Point) -> ZoomState:
    """Multiply zoom by factor, keeping the picture point under anchor fixed.

    anchor is in view pixels from the top-left corner.
    """
    zoom = min(MAX_ZOOM, max(MIN_ZOOM, state.zoom * factor))
    if not _valid(view, media):
        return replace(state, zoom=zoom)
    old_w, old_h = shown_size(view, media, state.zoom)
    new_w, new_h = shown_size(view, media, zoom)
    # Anchor relative to the view centre, then as a fraction of the old picture.
    cx, cy = anchor[0] - view[0] / 2, anchor[1] - view[1] / 2
    u = (cx - state.pan_x * old_w) / old_w
    v = (cy - state.pan_y * old_h) / old_h
    # The same picture point must stay under the anchor at the new size.
    pan_x = (cx - u * new_w) / new_w
    pan_y = (cy - v * new_h) / new_h
    return clamp(ZoomState(zoom, pan_x, pan_y), view, media)


def wheel_factor(notches: float) -> float:
    return WHEEL_FACTOR**notches


def pan_by(state: ZoomState, view: Size, media: Size, dx: float, dy: float) -> ZoomState:
    """Move the picture by (dx, dy) view pixels, clamped at the edges."""
    if not _valid(view, media):
        return state
    shown_w, shown_h = shown_size(view, media, state.zoom)
    return clamp(
        replace(state, pan_x=state.pan_x + dx / shown_w, pan_y=state.pan_y + dy / shown_h),
        view,
        media,
    )


def mpv_values(state: ZoomState) -> tuple[float, float, float]:
    """(video-zoom, video-pan-x, video-pan-y) for mpv."""
    return math.log2(state.zoom), state.pan_x, state.pan_y
