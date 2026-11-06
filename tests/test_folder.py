from __future__ import annotations

import ctypes
import os
from pathlib import Path

from glancer.explorer_sort import parse_sort_columns
from glancer.folder import FolderNavigator, list_media, sort_media
from glancer.settings import SortKey, SortOrder
from tests.conftest import wait_until

FILE_ATTRIBUTE_HIDDEN = 0x2


def make(folder: Path, name: str, size: int, mtime: float, created: float | None = None) -> Path:
    path = folder / name
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    if created is not None:  # files made in one clock tick share a creation time
        import pywintypes
        import win32file

        handle = win32file.CreateFile(
            str(path), win32file.GENERIC_WRITE, 0, None, win32file.OPEN_EXISTING, 0, None
        )
        try:
            win32file.SetFileTime(handle, pywintypes.Time(created), None, None)
        finally:
            handle.Close()
    return path


def names(paths: list[Path]) -> list[str]:
    return [p.name for p in paths]


def test_listing_and_sort_orders(tmp_path: Path) -> None:
    t = 1_700_000_000
    make(tmp_path, "img10.jpg", 30, t + 1, created=t - 30)
    make(tmp_path, "img2.jpg", 10, t + 3, created=t - 20)
    make(tmp_path, "Img1.png", 20, t + 2, created=t - 10)
    make(tmp_path, "notes.txt", 5, t)
    make(tmp_path, "model.3mf", 5, t)
    hidden = make(tmp_path, "secret.jpg", 5, t)
    ctypes.windll.kernel32.SetFileAttributesW(str(hidden), FILE_ATTRIBUTE_HIDDEN)
    (tmp_path / "sub").mkdir()
    make(tmp_path / "sub", "inner.jpg", 5, t)

    files = list_media(tmp_path)
    assert sorted(names(list(files))) == ["Img1.png", "img10.jpg", "img2.jpg"]

    def order(key: SortKey, descending: bool = False) -> list[str]:
        return names(sort_media(files, SortOrder(key, descending)))

    assert order(SortKey.NAME) == ["Img1.png", "img2.jpg", "img10.jpg"]  # natural, like Explorer
    assert order(SortKey.NAME, True) == ["img10.jpg", "img2.jpg", "Img1.png"]
    assert order(SortKey.MODIFIED) == ["img10.jpg", "Img1.png", "img2.jpg"]
    assert order(SortKey.SIZE, True) == ["img10.jpg", "Img1.png", "img2.jpg"]
    # Equal keys keep natural name order in either direction.
    assert order(SortKey.TYPE) == ["img2.jpg", "img10.jpg", "Img1.png"]
    assert order(SortKey.TYPE, True) == ["Img1.png", "img2.jpg", "img10.jpg"]
    assert order(SortKey.CREATED) == ["img10.jpg", "img2.jpg", "Img1.png"]


def test_parse_explorer_sort_columns() -> None:
    assert parse_sort_columns("prop:System.ItemNameDisplay;") == SortOrder(SortKey.NAME)
    assert parse_sort_columns("prop:-System.DateModified;") == SortOrder(SortKey.MODIFIED, True)
    assert parse_sort_columns("prop:+System.Size;System.ItemNameDisplay;") == SortOrder(
        SortKey.SIZE
    )
    assert parse_sort_columns("prop:-System.DateCreated;") == SortOrder(SortKey.CREATED, True)
    assert parse_sort_columns("prop:System.ItemTypeText;") == SortOrder(SortKey.TYPE)
    # Columns Glancer can't reproduce (e.g. Date taken) fall back to the default.
    assert parse_sort_columns("prop:-System.Photo.DateTaken;") is None
    assert parse_sort_columns("") is None


def test_navigation_boundaries_and_folder_changes(qapp, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    a, b, c = (make(tmp_path, n, 1, 1_700_000_000) for n in ("a1.jpg", "a2.jpg", "a10.jpg"))
    nav = FolderNavigator()
    nav.set_folder(tmp_path, SortOrder(SortKey.NAME))
    assert nav.neighbor(a, -1) is None and nav.neighbor(c, 1) is None
    assert nav.neighbor(a, 1) == b and nav.neighbor(c, -1) == b

    changes: list[bool] = []
    nav.changed.connect(lambda: changes.append(True))
    added = make(tmp_path, "a3.jpg", 1, 1_700_000_000)
    assert wait_until(lambda: bool(changes), 3)
    assert nav.neighbor(b, 1) == added

    # The current file is deleted: next is the file that took its place.
    changes.clear()
    added.unlink()
    assert wait_until(lambda: bool(changes), 3)
    assert nav.neighbor(added, 1) == c and nav.neighbor(added, -1) == b

    nav.set_order(SortOrder(SortKey.NAME, descending=True))
    assert nav.neighbor(c, 1) == b
