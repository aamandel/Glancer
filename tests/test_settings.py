from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray, QSettings

from glancer.settings import ALLOWED_KEYS, DEFAULT_VOLUME, EditMode, Settings, SortKey, SortOrder


def make_store(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "glancer.ini"), QSettings.Format.IniFormat)


def test_defaults_and_round_trip(tmp_path: Path) -> None:
    settings = Settings(make_store(tmp_path))
    assert settings.volume == DEFAULT_VOLUME and settings.muted is False
    assert settings.window_geometry is None
    assert settings.default_sort == SortOrder(SortKey.CREATED, descending=False)
    assert settings.default_edit_mode is EditMode.PRECISE

    settings.volume = 250  # clamped
    settings.muted = True
    settings.window_geometry = QByteArray(b"\x01\x02")
    settings.default_sort = SortOrder(SortKey.NAME, descending=True)
    settings.default_edit_mode = EditMode.PRECISE_LOSSLESS
    settings.sync()
    assert settings.volume == 100 and settings.muted is True
    assert settings.window_geometry == QByteArray(b"\x01\x02")
    assert settings.default_sort == SortOrder(SortKey.NAME, descending=True)
    assert settings.default_edit_mode is EditMode.PRECISE_LOSSLESS


def test_only_allow_listed_keys_are_stored(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.setValue("lastFolder", "C:/private")
    store.setValue("volume", 40)
    settings = Settings(store)
    with pytest.raises(KeyError):
        settings._write("recent/files", "C:/private/photo.jpg")
    settings.purge_unknown_keys()
    assert set(store.allKeys()) <= ALLOWED_KEYS
    assert settings.volume == 40


def test_corrupt_values_fall_back_to_defaults(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.setValue("volume", "loud")
    store.setValue("sort/default", "C:/some/path")
    store.setValue("edit/default_mode", "bogus")
    settings = Settings(store)
    assert settings.volume == DEFAULT_VOLUME
    assert settings.default_sort == SortOrder()
    assert settings.default_edit_mode is EditMode.PRECISE
