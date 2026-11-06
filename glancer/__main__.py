from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Glancer")
    app.setOrganizationName("Glancer")

    try:
        from glancer.main_window import MainWindow
    except RuntimeError as exc:  # LibmpvMissingError
        QMessageBox.critical(None, "Glancer", str(exc))
        return 1

    from glancer.utils import first_file_argument

    window = MainWindow(first_file_argument(sys.argv[1:]))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
