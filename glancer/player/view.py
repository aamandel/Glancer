"""OpenGL widget that renders libmpv frames and lets Qt paint on top.

mpv renders through its render API into this widget's framebuffer, so Qt can
draw controls, the crop rectangle and badges over the video (impossible with
mpv's own native window via --wid).

    paintGL:
        painter.beginNativePainting()
        mpv renders the frame into defaultFramebufferObject()
        painter.endNativePainting()
        paint_overlay(painter)      # subclasses / later phases draw here
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from PySide6.QtGui import QPainter
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from glancer.player.libmpv import mpv
from glancer.player.mpv_player import MpvPlayer
from glancer.player.thread_bridge import ThreadBridge

_opengl32 = ctypes.WinDLL("opengl32")
_kernel32 = ctypes.WinDLL("kernel32")
_wglGetProcAddress = _opengl32.wglGetProcAddress
_wglGetProcAddress.restype = ctypes.c_void_p
_wglGetProcAddress.argtypes = [ctypes.c_char_p]
_GetProcAddress = _kernel32.GetProcAddress
_GetProcAddress.restype = ctypes.c_void_p
_GetProcAddress.argtypes = [wintypes.HMODULE, ctypes.c_char_p]

# ctypes callback stubs handed to mpv must outlive any call mpv might still be
# making into them; a garbage-collected stub would leave mpv calling freed
# memory. They are tiny and there is normally one view per run, so they are
# simply never released.
_KEEP_ALIVE: list[object] = []

# wglGetProcAddress returns these small values instead of NULL on failure.
_INVALID_WGL_ADDRESSES = {0, 1, 2, 3, ctypes.c_void_p(-1).value}


def _get_proc_address(_ctx: Any, name: bytes) -> int:
    address = _wglGetProcAddress(name)
    if address in _INVALID_WGL_ADDRESSES or address is None:
        # OpenGL 1.1 functions are exported directly by opengl32.dll.
        address = _GetProcAddress(_opengl32._handle, name)
    return address or 0


class GlancerView(QOpenGLWidget):

    def __init__(self, player: MpvPlayer, parent: QWidget | None = None):
        super().__init__(parent)
        self._player = player
        self._render_context: Any = None
        self._released = False
        self._proc_address_fn = mpv.MpvGlGetProcAddressFn(_get_proc_address)
        _KEEP_ALIVE.append(self._proc_address_fn)
        self._frame_bridge: ThreadBridge[None] = ThreadBridge(self._on_frame_ready, self)
        self.setMinimumSize(160, 90)

    # -- GL lifecycle ---------------------------------------------------------

    def initializeGL(self) -> None:
        self._render_context = self._player.create_render_context(self._proc_address_fn)
        self._render_context.update_cb = self._on_mpv_update
        self.context().aboutToBeDestroyed.connect(self.release)

    def paintGL(self) -> None:
        painter = QPainter(self)
        if self._render_context is not None:
            ratio = self.devicePixelRatioF()
            painter.beginNativePainting()
            self._render_context.render(
                flip_y=True,
                opengl_fbo={
                    "w": round(self.width() * ratio),
                    "h": round(self.height() * ratio),
                    "fbo": self.defaultFramebufferObject(),
                },
            )
            painter.endNativePainting()
        self.paint_overlay(painter)
        painter.end()

    def paint_overlay(self, painter: QPainter) -> None:
        """Hook for drawing on top of the video (overlay, crop, badges)."""

    def _on_frame_ready(self, _items: list[None]) -> None:
        if self._render_context is not None:
            self._render_context.update()
            self.update()

    def _on_mpv_update(self) -> None:
        # Runs on mpv's render thread: never call mpv or Qt here.
        if not self._released:
            self._frame_bridge.post(None)

    def release(self) -> None:
        """Free the render context. Must run before the player terminates.

        mpv may invoke the update callback from its render thread while the
        context is being freed. Detach it at the C level first (NULL
        callback) so no Python code runs during teardown.
        """
        self._released = True
        self._frame_bridge.close()
        context, self._render_context = self._render_context, None
        if context is None:
            return
        self.makeCurrent()
        mpv._mpv_render_context_set_update_callback(
            context.handle, ctypes.cast(None, mpv.RenderUpdateFn), None
        )
        context.free()
        self.doneCurrent()
        # Keeps context._update_fn_wrapper (the update-callback stub) alive.
        _KEEP_ALIVE.append(context)
