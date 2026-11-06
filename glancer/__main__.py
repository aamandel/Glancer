from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from glancer.main_window import MainWindow
from glancer.utils import first_video_argument


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Glancer")
    app.setOrganizationName("Glancer")
    window = MainWindow(first_video_argument(sys.argv[1:]))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

