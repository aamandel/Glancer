from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

import pytest
from PySide6.QtCore import QEventLoop, QSettings, QTimer
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from glancer.main_window import MainWindow

MEDIA_DIR = Path(__file__).parent / "media"
CLICK_INTERVAL_MS = 200  # instead of the system's ~500 ms; test clicks are instant


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    from glancer.ui.theme import apply_theme

    apply_theme(app)  # type: ignore[arg-type]
    return app  # type: ignore[return-value]


def process_events(seconds: float) -> None:
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        process_events(0.02)
    return condition()


@pytest.fixture
def media() -> Callable[[str], Path]:
    def get(name: str) -> Path:
        path = MEDIA_DIR / name
        if not path.is_file():
            pytest.skip("Run tools/make_test_media.py to generate tests/media")
        return path.resolve()

    return get


@pytest.fixture(scope="session")
def shared_window(qapp, tmp_path_factory) -> Iterator["MainWindow"]:  # type: ignore[no-untyped-def]
    """One window (and mpv instance) for all GUI tests: creating one costs ~0.5 s."""
    from glancer.main_window import MainWindow
    from glancer.settings import Settings

    ini = tmp_path_factory.mktemp("settings") / "glancer.ini"
    win = MainWindow(settings=Settings(QSettings(str(ini), QSettings.Format.IniFormat)))
    win.resize(960, 620)
    win.show()
    process_events(0.2)
    yield win
    win.close()


@pytest.fixture
def window(shared_window: "MainWindow") -> "MainWindow":
    """The shared window, reset to a fresh start screen with default view settings."""
    from glancer.main_window import AUTO_HIDE_MS

    win = shared_window
    win.exit_fullscreen()
    win.clear()
    if win.overlay_locked:
        win.toggle_overlay_lock()
    win.auto_hide_ms = AUTO_HIDE_MS
    win.clicks.interval_ms = CLICK_INTERVAL_MS
    p = win.player
    p.set_speed(1.0)
    p.set_loop(False)
    p.set_rotation(0)
    p.set_muted(False)
    p.set_volume(70)
    assert wait_until(lambda: not win.isFullScreen())
    process_events(0.05)
    return win
