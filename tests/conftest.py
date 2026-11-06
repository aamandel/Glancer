from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

MEDIA_DIR = Path(__file__).parent / "media"


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
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

