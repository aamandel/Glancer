import math

import pytest

from glancer import zoom
from glancer.zoom import ZoomState

VIEW = (960.0, 620.0)
WIDE = (1920.0, 1080.0)  # fit is width-limited in VIEW: shown 960x540 at 100%


def screen_x(state: ZoomState, media_u: float) -> float:
    """Screen x of the picture point at fraction media_u (-0.5..0.5) from its centre."""
    shown_w, _ = zoom.shown_size(VIEW, WIDE, state.zoom)
    return VIEW[0] / 2 + state.pan_x * shown_w + media_u * shown_w


def test_pan_is_clamped_at_all_four_edges() -> None:
    # At fit nothing can pan.
    assert zoom.clamp(ZoomState(1.0, 0.3, -0.3), VIEW, WIDE) == ZoomState(1.0, 0.0, 0.0)

    state = ZoomState(4.0)  # shown 3840x2160
    limit_x = (3840 - 960) / 2 / 3840
    limit_y = (2160 - 620) / 2 / 2160
    for dx, dy, expected in [
        (1e6, 1e6, (limit_x, limit_y)),  # dragged right/down: top-left corner shows
        (-1e6, -1e6, (-limit_x, -limit_y)),  # bottom-right corner shows
        (1e6, -1e6, (limit_x, -limit_y)),
        (-1e6, 1e6, (-limit_x, limit_y)),
    ]:
        moved = zoom.pan_by(state, VIEW, WIDE, dx, dy)
        assert (moved.pan_x, moved.pan_y) == pytest.approx(expected)
    # A clamped picture edge sits exactly on the view edge.
    left = zoom.pan_by(state, VIEW, WIDE, 1e6, 0)
    assert screen_x(left, -0.5) == pytest.approx(0.0)

    assert zoom.clamp(ZoomState(40.0), VIEW, WIDE).zoom == zoom.MAX_ZOOM
    assert zoom.clamp(ZoomState(0.2), VIEW, WIDE).zoom == zoom.MIN_ZOOM


def test_wheel_zoom_keeps_the_point_under_the_cursor() -> None:
    state = ZoomState()
    anchor = (600.0, 300.0)
    u = (anchor[0] - screen_x(state, 0.0)) / zoom.shown_size(VIEW, WIDE, 1.0)[0]
    for notches in (1, 3, 2):
        state = zoom.zoom_at(state, VIEW, WIDE, zoom.wheel_factor(notches), anchor)
        assert screen_x(state, u) == pytest.approx(anchor[0])
    assert state.zoom == pytest.approx(1.25**6) and state.percent == 381
    assert zoom.mpv_values(state)[0] == pytest.approx(math.log2(1.25**6))

    # Zooming back out to fit re-centres the picture.
    state = zoom.zoom_at(state, VIEW, WIDE, zoom.wheel_factor(-20), anchor)
    assert state == ZoomState(1.0, 0.0, 0.0)


def test_portrait_media_pans_only_where_it_overflows() -> None:
    portrait = (1080.0, 1920.0)  # e.g. a phone photo, or a wide one rotated 90°
    # Height-limited fit: 100% is 349x620, so at 200% (698 wide) only y overflows.
    state = zoom.pan_by(ZoomState(2.0), VIEW, portrait, 500, 500)
    assert state.pan_x == 0.0 and state.pan_y == pytest.approx((1240 - 620) / 2 / 1240)
    # Pan is a fraction of the picture, so it survives a window resize, re-clamped.
    bigger = zoom.clamp(state, (1920.0, 1200.0), portrait)
    assert bigger.pan_y == pytest.approx((2400 - 1200) / 2 / 2400)
