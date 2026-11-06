"""Fullscreen that Windows keeps compositing.

An OpenGL window whose client area exactly matches a monitor is treated as
"fullscreen" and presented directly to the display, bypassing the desktop
compositor. Then other top-level windows such as popup menus never appear
over it, and with variable refresh (G-Sync) the screen - mouse cursor
included - only refreshes when the app draws, so a paused video makes the
cursor crawl.

So the client area is made one row taller than the monitor (that row sits
below the screen edge). Qt's own fullscreen state can't be used for this: Qt
drops the state as soon as the window no longer matches the screen exactly,
after which leaving fullscreen does nothing. Instead this is plain Win32:

    enter:  remember style + placement (normal rect, maximized or not)
            style: no caption or sizing frame, plus a 1 px WS_BORDER
            window rect = monitor grown by 1 px left/top/right and 2 px at
                          the bottom (border off-screen, client = monitor + 1 row)
    leave:  put the style and placement back
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

GWL_STYLE = -16
WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_MAXIMIZE = 0x01000000
_FRAME_STYLES = WS_DLGFRAME | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_MAXIMIZE
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020
MONITOR_DEFAULTTONEAREST = 2

_user32 = ctypes.windll.user32
_user32.GetWindowLongW.restype = wintypes.LONG
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetWindowLongW.restype = wintypes.LONG
_user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
_user32.MonitorFromWindow.restype = wintypes.HMONITOR
_user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class _WindowPlacement(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT)]


@dataclass
class SavedWindow:
    style: int
    placement: _WindowPlacement


def enter_fullscreen(hwnd: int) -> SavedWindow | None:
    """Cover the window's monitor; returns what leave_fullscreen() needs."""
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    if not _user32.GetMonitorInfoW(_user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST),
                                   ctypes.byref(info)):
        return None
    placement = _WindowPlacement()
    placement.length = ctypes.sizeof(_WindowPlacement)
    _user32.GetWindowPlacement(hwnd, ctypes.byref(placement))
    style = _user32.GetWindowLongW(hwnd, GWL_STYLE)
    _user32.SetWindowLongW(hwnd, GWL_STYLE, (style & ~_FRAME_STYLES) | WS_BORDER)
    r = info.rcMonitor
    _user32.SetWindowPos(hwnd, None, r.left - 1, r.top - 1, r.right - r.left + 2,
                         r.bottom - r.top + 3, SWP_NOZORDER | SWP_FRAMECHANGED)
    return SavedWindow(style, placement)


def leave_fullscreen(hwnd: int, saved: SavedWindow) -> None:
    # The placement's show command brings back "maximized" if it was.
    _user32.SetWindowLongW(hwnd, GWL_STYLE, saved.style & ~WS_MAXIMIZE)
    _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                         SWP_NOZORDER | SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED)
    _user32.SetWindowPlacement(hwnd, ctypes.byref(saved.placement))
