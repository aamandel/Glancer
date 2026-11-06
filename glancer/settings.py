"""Persistent preferences, limited to a fixed allow-list of non-identifying keys.

Glancer must never store file paths, file names, or any history. This module is
the only place that touches QSettings, and it exposes typed accessors for each
allowed key instead of a generic ``set(key, value)``. Every value is validated
before it is written, so nothing path-like can reach the store by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QByteArray, QSettings

ORGANIZATION = "Glancer"
APPLICATION = "Glancer"


class SortKey(str, Enum):
    NAME = "name"
    CREATED = "created"
    MODIFIED = "modified"
    SIZE = "size"
    TYPE = "type"


class EditMode(str, Enum):
    PRECISE = "precise"
    FAST_LOSSLESS = "fast_lossless"
    PRECISE_LOSSLESS = "precise_lossless"


@dataclass(frozen=True)
class SortOrder:
    key: SortKey = SortKey.CREATED
    descending: bool = False

    def serialize(self) -> str:
        return f"{self.key.value}:{'desc' if self.descending else 'asc'}"

    @classmethod
    def parse(cls, raw: object) -> "SortOrder":
        if isinstance(raw, str) and raw.count(":") == 1:
            key, direction = raw.split(":")
            try:
                sort_key = SortKey(key)
            except ValueError:
                return cls()
            if direction in ("asc", "desc"):
                return cls(sort_key, direction == "desc")
        return cls()


# The complete set of keys Glancer may write. Anything else is rejected.
ALLOWED_KEYS = frozenset(
    {
        "volume",
        "muted",
        "window/geometry",
        "sort/default",
        "edit/default_mode",
    }
)

DEFAULT_VOLUME = 70


class Settings:
    """Typed, allow-listed access to Glancer's preferences."""

    def __init__(self, store: QSettings | None = None):
        self._store = store if store is not None else QSettings(
            QSettings.Format.NativeFormat,
            QSettings.Scope.UserScope,
            ORGANIZATION,
            APPLICATION,
        )

    # -- internal -----------------------------------------------------------

    def _write(self, key: str, value: object) -> None:
        if key not in ALLOWED_KEYS:
            raise KeyError(f"Setting '{key}' is not in the allow-list")
        self._store.setValue(key, value)

    def _read(self, key: str, default: object = None) -> object:
        if key not in ALLOWED_KEYS:
            raise KeyError(f"Setting '{key}' is not in the allow-list")
        return self._store.value(key, default)

    def sync(self) -> None:
        self._store.sync()

    def purge_unknown_keys(self) -> None:
        """Remove anything in the store that isn't allow-listed."""
        for key in self._store.allKeys():
            if key not in ALLOWED_KEYS:
                self._store.remove(key)

    # -- volume ---------------------------------------------------------------

    @property
    def volume(self) -> int:
        try:
            value = int(float(self._read("volume", DEFAULT_VOLUME)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return DEFAULT_VOLUME
        return max(0, min(100, value))

    @volume.setter
    def volume(self, value: int) -> None:
        self._write("volume", max(0, min(100, int(value))))

    @property
    def muted(self) -> bool:
        raw = self._read("muted", False)
        if isinstance(raw, str):
            return raw.lower() == "true"
        return bool(raw)

    @muted.setter
    def muted(self, value: bool) -> None:
        self._write("muted", bool(value))

    # -- window ---------------------------------------------------------------

    @property
    def window_geometry(self) -> QByteArray | None:
        raw = self._read("window/geometry")
        return raw if isinstance(raw, QByteArray) and not raw.isEmpty() else None

    @window_geometry.setter
    def window_geometry(self, value: QByteArray) -> None:
        if not isinstance(value, QByteArray):
            raise TypeError("window geometry must be a QByteArray from saveGeometry()")
        self._write("window/geometry", value)

    # -- sort -----------------------------------------------------------------

    @property
    def default_sort(self) -> SortOrder:
        return SortOrder.parse(self._read("sort/default"))

    @default_sort.setter
    def default_sort(self, value: SortOrder) -> None:
        self._write("sort/default", value.serialize())

    # -- editor ---------------------------------------------------------------

    @property
    def default_edit_mode(self) -> EditMode:
        try:
            return EditMode(self._read("edit/default_mode", EditMode.PRECISE.value))
        except ValueError:
            return EditMode.PRECISE

    @default_edit_mode.setter
    def default_edit_mode(self, value: EditMode) -> None:
        self._write("edit/default_mode", EditMode(value).value)
