from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from framestepper.main_window import MainWindow
from framestepper.utils import first_video_argument


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("FrameStepper")
    app.setOrganizationName("FrameStepper")
    window = MainWindow(first_video_argument(sys.argv[1:]))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

