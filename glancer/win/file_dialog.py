"""The native Windows "Open" dialog, told not to record what you open.

Qt's QFileDialog uses the same Windows dialog but without
FOS_DONTADDTORECENT, so Windows adds every opened file to Recent items and
Quick Access. Here the dialog is driven directly through COM (comtypes):

    dialog = CoCreateInstance(FileOpenDialog)
    dialog.SetOptions(defaults | FOS_DONTADDTORECENT | FOS_FORCEFILESYSTEM ...)
    dialog.SetFileTypes(All media / Videos / Photos / Audio)
    dialog.SetFolder(current media's folder)          # only when a file is open
    dialog.Show(owner) -> GetResult() -> path, or None if cancelled
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_int, c_uint, c_ulong, c_void_p, wintypes
from pathlib import Path

import comtypes
import comtypes.client
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

from glancer.media_types import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    VIDEO_EXTENSIONS,
)

FOS_OVERWRITEPROMPT = 0x2
FOS_NOCHANGEDIR = 0x8
FOS_FORCEFILESYSTEM = 0x40
FOS_PATHMUSTEXIST = 0x800
FOS_FILEMUSTEXIST = 0x1000
FOS_DONTADDTORECENT = 0x02000000
SIGDN_FILESYSPATH = 0x80058000
ERROR_CANCELLED_HRESULT = -2147023673  # HRESULT_FROM_WIN32(ERROR_CANCELLED)

CLSID_FILE_OPEN_DIALOG = GUID("{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}")
OPEN_OPTIONS = (
    FOS_DONTADDTORECENT | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST
    | FOS_FILEMUSTEXIST | FOS_NOCHANGEDIR
)


class COMDLG_FILTERSPEC(ctypes.Structure):
    _fields_ = [("pszName", wintypes.LPCWSTR), ("pszSpec", wintypes.LPCWSTR)]


class IShellItem(IUnknown):
    _iid_ = GUID("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")


IShellItem._methods_ = [
    COMMETHOD([], HRESULT, "BindToHandler", (["in"], c_void_p, "pbc"),
              (["in"], POINTER(GUID), "bhid"), (["in"], POINTER(GUID), "riid"),
              (["out"], POINTER(c_void_p), "ppv")),
    COMMETHOD([], HRESULT, "GetParent", (["out"], POINTER(POINTER(IShellItem)), "ppsi")),
    COMMETHOD([], HRESULT, "GetDisplayName", (["in"], c_ulong, "sigdnName"),
              (["out"], POINTER(c_void_p), "ppszName")),
    COMMETHOD([], HRESULT, "GetAttributes", (["in"], c_ulong, "sfgaoMask"),
              (["out"], POINTER(c_ulong), "psfgaoAttribs")),
    COMMETHOD([], HRESULT, "Compare", (["in"], POINTER(IShellItem), "psi"),
              (["in"], c_ulong, "hint"), (["out"], POINTER(c_int), "piOrder")),
]


class IFileOpenDialog(IUnknown):
    """IModalWindow + IFileDialog + IFileOpenDialog methods, in vtable order."""

    _iid_ = GUID("{D57C7288-D4AD-4768-BE02-9D969532D960}")
    _methods_ = [
        # IModalWindow
        COMMETHOD([], HRESULT, "Show", (["in"], wintypes.HWND, "hwndOwner")),
        # IFileDialog
        COMMETHOD([], HRESULT, "SetFileTypes", (["in"], c_uint, "cFileTypes"),
                  (["in"], POINTER(COMDLG_FILTERSPEC), "rgFilterSpec")),
        COMMETHOD([], HRESULT, "SetFileTypeIndex", (["in"], c_uint, "iFileType")),
        COMMETHOD([], HRESULT, "GetFileTypeIndex", (["out"], POINTER(c_uint), "piFileType")),
        COMMETHOD([], HRESULT, "Advise", (["in"], c_void_p, "pfde"),
                  (["out"], POINTER(wintypes.DWORD), "pdwCookie")),
        COMMETHOD([], HRESULT, "Unadvise", (["in"], wintypes.DWORD, "dwCookie")),
        COMMETHOD([], HRESULT, "SetOptions", (["in"], wintypes.DWORD, "fos")),
        COMMETHOD([], HRESULT, "GetOptions", (["out"], POINTER(wintypes.DWORD), "pfos")),
        COMMETHOD([], HRESULT, "SetDefaultFolder", (["in"], POINTER(IShellItem), "psi")),
        COMMETHOD([], HRESULT, "SetFolder", (["in"], POINTER(IShellItem), "psi")),
        COMMETHOD([], HRESULT, "GetFolder", (["out"], POINTER(POINTER(IShellItem)), "ppsi")),
        COMMETHOD([], HRESULT, "GetCurrentSelection",
                  (["out"], POINTER(POINTER(IShellItem)), "ppsi")),
        COMMETHOD([], HRESULT, "SetFileName", (["in"], wintypes.LPCWSTR, "pszName")),
        COMMETHOD([], HRESULT, "GetFileName", (["out"], POINTER(c_void_p), "pszName")),
        COMMETHOD([], HRESULT, "SetTitle", (["in"], wintypes.LPCWSTR, "pszTitle")),
        COMMETHOD([], HRESULT, "SetOkButtonLabel", (["in"], wintypes.LPCWSTR, "pszText")),
        COMMETHOD([], HRESULT, "SetFileNameLabel", (["in"], wintypes.LPCWSTR, "pszLabel")),
        COMMETHOD([], HRESULT, "GetResult", (["out"], POINTER(POINTER(IShellItem)), "ppsi")),
        COMMETHOD([], HRESULT, "AddPlace", (["in"], POINTER(IShellItem), "psi"),
                  (["in"], c_int, "fdap")),
        COMMETHOD([], HRESULT, "SetDefaultExtension",
                  (["in"], wintypes.LPCWSTR, "pszDefaultExtension")),
        COMMETHOD([], HRESULT, "Close", (["in"], HRESULT, "hr")),
        COMMETHOD([], HRESULT, "SetClientGuid", (["in"], POINTER(GUID), "guid")),
        COMMETHOD([], HRESULT, "ClearClientData"),
        COMMETHOD([], HRESULT, "SetFilter", (["in"], c_void_p, "pFilter")),
        # IFileOpenDialog
        COMMETHOD([], HRESULT, "GetResults", (["out"], POINTER(c_void_p), "ppenum")),
        COMMETHOD([], HRESULT, "GetSelectedItems", (["out"], POINTER(c_void_p), "ppsai")),
    ]


_shell32 = ctypes.WinDLL("shell32")
_SHCreateItemFromParsingName = _shell32.SHCreateItemFromParsingName
_SHCreateItemFromParsingName.argtypes = [
    wintypes.LPCWSTR, c_void_p, POINTER(GUID), POINTER(POINTER(IShellItem))
]
_SHCreateItemFromParsingName.restype = HRESULT
_CoTaskMemFree = ctypes.WinDLL("ole32").CoTaskMemFree
_CoTaskMemFree.argtypes = [c_void_p]


def _patterns(extensions: frozenset[str]) -> str:
    return ";".join(f"*{ext}" for ext in sorted(extensions))


FILTERS = [
    ("All media", _patterns(SUPPORTED_EXTENSIONS)),
    ("Videos", _patterns(VIDEO_EXTENSIONS)),
    ("Photos", _patterns(IMAGE_EXTENSIONS)),
    ("Audio", _patterns(AUDIO_EXTENSIONS)),
]


def _shell_item(folder: Path) -> POINTER(IShellItem) | None:  # type: ignore[valid-type]
    item = POINTER(IShellItem)()
    try:
        _SHCreateItemFromParsingName(str(folder), None, byref(IShellItem._iid_), byref(item))
    except OSError:
        return None
    return item


def _item_path(item: POINTER(IShellItem)) -> Path:  # type: ignore[valid-type]
    raw = item.GetDisplayName(SIGDN_FILESYSPATH)
    try:
        return Path(ctypes.wstring_at(raw))
    finally:
        _CoTaskMemFree(raw)


def create_open_dialog(start_folder: Path | None) -> POINTER(IFileOpenDialog):  # type: ignore[valid-type]
    """Build (but don't show) the dialog; separate so tests can check options."""
    dialog = comtypes.client.CreateObject(CLSID_FILE_OPEN_DIALOG, interface=IFileOpenDialog)
    dialog.SetOptions(dialog.GetOptions() | OPEN_OPTIONS)
    specs = (COMDLG_FILTERSPEC * len(FILTERS))(*FILTERS)
    dialog.SetFileTypes(len(FILTERS), specs)
    dialog.SetFileTypeIndex(1)
    dialog.SetTitle("Open file")
    if start_folder is not None and start_folder.is_dir():
        item = _shell_item(start_folder)
        if item:
            dialog.SetFolder(item)
    return dialog


def get_open_path(owner_hwnd: int, start_folder: Path | None) -> Path | None:
    """Show the dialog modally. Returns the chosen file, or None if cancelled."""
    dialog = create_open_dialog(start_folder)
    try:
        dialog.Show(owner_hwnd)
    except comtypes.COMError as exc:
        if exc.hresult == ERROR_CANCELLED_HRESULT:
            return None
        raise
    return _item_path(dialog.GetResult())
