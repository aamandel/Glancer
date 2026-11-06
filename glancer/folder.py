"""The media files in one folder, in a chosen order, for previous/next.

    list_media(folder)          supported, non-hidden files (no subfolders)
    sort_media(files, order)    by the order's key; ties broken by natural name
    FolderNavigator             caches the sorted list; QFileSystemWatcher marks
                                it stale when files are added, renamed or deleted

Name order uses StrCmpLogicalW, the same comparison Explorer uses, so
"img2" comes before "img10".
"""

from __future__ import annotations

import ctypes
import functools
import os
import stat
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, Signal

from glancer.media_types import is_supported
from glancer.settings import SortKey, SortOrder

_StrCmpLogicalW = ctypes.WinDLL("shlwapi").StrCmpLogicalW
_StrCmpLogicalW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
_StrCmpLogicalW.restype = ctypes.c_int


def natural_compare(a: str, b: str) -> int:
    return _StrCmpLogicalW(a, b)


_natural_key = functools.cmp_to_key(natural_compare)


def _is_hidden(st: os.stat_result) -> bool:
    attributes = getattr(st, "st_file_attributes", 0)
    return bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))


def list_media(folder: Path) -> dict[Path, os.stat_result]:
    """Supported, visible files directly in folder, with their stat results."""
    found: dict[Path, os.stat_result] = {}
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return found
    for entry in entries:
        try:
            if not entry.is_file() or not is_supported(Path(entry.name)):
                continue
            st = entry.stat()
        except OSError:
            continue  # vanished or unreadable
        if not _is_hidden(st):
            found[Path(entry.path)] = st
    return found


def _created(st: os.stat_result) -> float:
    # On Windows st_ctime is the creation time (st_birthtime from Python 3.12).
    return getattr(st, "st_birthtime", st.st_ctime)


def sort_media(files: dict[Path, os.stat_result], order: SortOrder) -> list[Path]:
    primary = {
        SortKey.CREATED: lambda p: _created(files[p]),
        SortKey.MODIFIED: lambda p: files[p].st_mtime,
        SortKey.SIZE: lambda p: files[p].st_size,
        SortKey.TYPE: lambda p: p.suffix.lower(),
    }.get(order.key)
    # Name first (always ascending), then a stable sort by the primary key,
    # so equal keys stay in natural name order in either direction.
    by_name = sorted(files, key=lambda p: _natural_key(p.name), reverse=(
        order.key is SortKey.NAME and order.descending
    ))
    if primary is None:
        return by_name
    return sorted(by_name, key=primary, reverse=order.descending)


class FolderNavigator(QObject):
    """Previous/next within the current file's folder."""

    changed = Signal()  # files were added, removed or renamed

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._folder: Path | None = None
        self._order = SortOrder()
        self._files: list[Path] | None = None  # None = needs re-listing
        self._positions: dict[Path, int] = {}
        self._gone: dict[Path, int] = {}  # vanished file -> its old index
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._folder_changed)

    @property
    def folder(self) -> Path | None:
        return self._folder

    @property
    def order(self) -> SortOrder:
        return self._order

    def set_folder(self, folder: Path, order: SortOrder) -> None:
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        self._folder = folder
        self._order = order
        self._files = None
        self._gone.clear()
        self._watcher.addPath(str(folder))

    def set_order(self, order: SortOrder) -> None:
        if order != self._order:
            self._order = order
            self._files = None
            self._gone.clear()

    def files(self) -> list[Path]:
        if self._files is None:
            if self._folder is None:
                return []
            self._files = sort_media(list_media(self._folder), self._order)
            self._positions = {p: i for i, p in enumerate(self._files)}
        return self._files

    def neighbor(self, current: Path, direction: int) -> Path | None:
        """The next (+1) or previous (-1) file, or None at either end."""
        files = self.files()
        index = self._positions.get(current)
        if index is not None:
            target = index + direction
        elif current in self._gone:
            # The current file was deleted or renamed: "next" is the file that
            # slid into its old place, "previous" the one before that.
            old = self._gone[current]
            target = old if direction > 0 else old - 1
        else:
            return None
        return files[target] if 0 <= target < len(files) else None

    def _folder_changed(self, _path: str) -> None:
        old_positions = self._positions
        self._files = None
        self.files()
        for path, index in old_positions.items():
            if path not in self._positions:
                self._gone[path] = index
        self.changed.emit()
