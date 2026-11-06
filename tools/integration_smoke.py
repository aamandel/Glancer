"""Launch the real player briefly and verify its mpv connection.

Usage: python tools/integration_smoke.py path-to-video
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from framestepper.main_window import MainWindow


def main() -> int:
    if len(sys.argv) != 2:
        print("Expected one video path.")
        return 2

    app = QApplication(sys.argv)
    window = MainWindow(Path(sys.argv[1]).resolve())
    window.show()
    started = time.monotonic()
    state = "loading"
    position_before_step = 0.0
    result = {"code": 1}

    def check() -> None:
        nonlocal state, position_before_step
        if time.monotonic() - started > 30:
            controller = window._controller
            pending = controller.pending_response_count if controller is not None else -1
            print(
                f"Timed out in state: {state}; position={window._position:.3f}; "
                f"pending_responses={pending}"
            )
            window.close()
            app.exit(1)
            return

        if state == "loading" and window._duration > 0:
            print(f"Loaded video; duration={window._duration:.3f}s")
            assert window._controller is not None
            for iteration in range(300):
                window._controller.set_paused(True)
                if iteration % 10 == 0:
                    window._controller.seek((iteration % 15) / 10)
            for _ in range(5):
                window._controller.step_forward()
                window._controller.step_backward()
            print("Queued 340 pause, seek, and frame commands for stress coverage")
            state = "draining"
        elif state == "draining" and window._controller is not None:
            if window._controller.pending_response_count == 0:
                print("All stress commands were acknowledged by mpv")
                window._controller.seek(0.0)
                state = "resetting"
        elif state == "resetting" and window._controller is not None:
            if (
                window._position <= 0.05
                and window._controller.pending_response_count == 0
            ):
                window._controller.seek(0.5)
                state = "seeking"
        elif state == "seeking" and window._controller is not None and 0.45 <= window._position <= 0.55:
            if window._controller.pending_response_count:
                return
            position_before_step = window._position
            window.step_forward()
            state = "forward"
        elif state == "forward" and window._position > position_before_step:
            print(
                f"Forward step moved {position_before_step:.3f}s -> "
                f"{window._position:.3f}s"
            )
            position_before_step = window._position
            window.step_backward()
            state = "backward"
        elif state == "backward" and window._position < position_before_step:
            print(
                f"Backward step moved {position_before_step:.3f}s -> "
                f"{window._position:.3f}s"
            )
            result["code"] = 0
            window.close()
            app.exit(0)

    timer = QTimer()
    timer.timeout.connect(check)
    timer.start(50)
    app.exec()
    return result["code"]


if __name__ == "__main__":
    raise SystemExit(main())
