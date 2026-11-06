"""Copy the sort order of an open Explorer window showing the same folder.

    for each window in Shell.Application.Windows()      # includes Win11 tabs
        if its folder is ours: parse its SortColumns    # "prop:-System.DateModified;"
    none found, or an unknown column -> None (caller uses the saved default)

The query runs on its own thread (COM initialised there) and the caller waits
at most a short timeout, so a hung Explorer can't stall Glancer.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from glancer.settings import SortKey, SortOrder

QUERY_TIMEOUT_S = 1.5

_COLUMNS = {
    "system.itemnamedisplay": SortKey.NAME,
    "system.itemnamedisplaywithoutextension": SortKey.NAME,
    "system.filename": SortKey.NAME,
    "system.datecreated": SortKey.CREATED,
    "system.datemodified": SortKey.MODIFIED,
    "system.size": SortKey.SIZE,
    "system.itemtypetext": SortKey.TYPE,
    "system.itemtype": SortKey.TYPE,
    "system.filetype": SortKey.TYPE,
}


def parse_sort_columns(text: str) -> SortOrder | None:
    """Map Explorer's SortColumns string to a SortOrder, using its first column."""
    text = (text or "").strip()
    if text.lower().startswith("prop:"):
        text = text[5:]
    first = text.split(";")[0].strip()
    if not first:
        return None
    descending = first.startswith("-")
    key = _COLUMNS.get(first.lstrip("+-").lower())
    return SortOrder(key, descending) if key is not None else None


def _same_folder(a: str, b: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except (TypeError, ValueError):
        return False


def _query(folder: Path) -> SortOrder | None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        for window in shell.Windows():
            try:
                document = window.Document
                path = document.Folder.Self.Path
                if path and _same_folder(path, folder):
                    return parse_sort_columns(document.SortColumns)
            except Exception:  # not a folder view (e.g. Internet Explorer), or closing
                continue
        return None
    finally:
        pythoncom.CoUninitialize()


class ExplorerSortQuery:
    """Starts the lookup immediately; result() waits briefly for it."""

    def __init__(self, folder: Path):
        self.folder = folder
        self._result: SortOrder | None = None
        self._thread = threading.Thread(target=self._run, name="explorer-sort", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._result = _query(self.folder)
        except Exception:
            self._result = None

    def result(self, timeout: float = QUERY_TIMEOUT_S) -> SortOrder | None:
        self._thread.join(timeout)
        return None if self._thread.is_alive() else self._result
