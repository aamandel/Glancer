from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray, QSettings

from glancer.settings import (
    ALLOWED_KEYS,
    DEFAULT_VOLUME,
    EditMode,
    Settings,
    SortKey,
    SortOrder,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    store = QSettings(str(tmp_path / "glancer.ini"), QSettings.Format.IniFormat)
    return Settings(store)


def test_defaults(settings: Settings) -> None:
    assert settings.volume == DEFAULT_VOLUME
    assert settings.muted is False
    assert settings.window_geometry is None
    assert settings.default_sort == SortOrder(SortKey.CREATED, descending=False)
    assert settings.default_edit_mode is EditMode.PRECISE


def test_round_trip(settings: Settings) -> None:
    settings.volume = 35
    settings.muted = True
    settings.window_geometry = QByteArray(b"\x01\x02")
    settings.default_sort = SortOrder(SortKey.NAME, descending=True)
    settings.default_edit_mode = EditMode.PRECISE_LOSSLESS
    settings.sync()

    assert settings.volume == 35
    assert settings.muted is True
    assert settings.window_geometry == QByteArray(b"\x01\x02")
    assert settings.default_sort == SortOrder(SortKey.NAME, descending=True)
    assert settings.default_edit_mode is EditMode.PRECISE_LOSSLESS


def test_volume_is_clamped(settings: Settings) -> None:
    settings.volume = 250
    assert settings.volume == 100
    settings.volume = -5
    assert settings.volume == 0


def test_unknown_keys_are_rejected(settings: Settings) -> None:
    with pytest.raises(KeyError):
        settings._write("recent/files", "C:/private/photo.jpg")


def test_purge_removes_unknown_keys(tmp_path: Path) -> None:
    store = QSettings(str(tmp_path / "glancer.ini"), QSettings.Format.IniFormat)
    store.setValue("lastFolder", "C:/private")
    store.setValue("volume", 40)
    Settings(store).purge_unknown_keys()
    assert set(store.allKeys()) <= ALLOWED_KEYS
    assert store.value("volume") in (40, "40")


def test_corrupt_values_fall_back_to_defaults(tmp_path: Path) -> None:
    store = QSettings(str(tmp_path / "glancer.ini"), QSettings.Format.IniFormat)
    store.setValue("volume", "loud")
    store.setValue("sort/default", "C:/some/path")
    store.setValue("edit/default_mode", "bogus")
    settings = Settings(store)
    assert settings.volume == DEFAULT_VOLUME
    assert settings.default_sort == SortOrder()
    assert settings.default_edit_mode is EditMode.PRECISE
