"""Import python-mpv with Glancer's bundled libmpv-2.dll.

python-mpv locates the DLL by searching PATH at import time, so the bundled
folders are prepended to PATH first. Import ``mpv`` through this module only.
"""

from __future__ import annotations

import os

from glancer.utils import binary_dirs

LIBMPV_NAME = "libmpv-2.dll"


class LibmpvMissingError(RuntimeError):
    pass


def _prepare_path() -> None:
    for folder in reversed(binary_dirs()):
        if (folder / LIBMPV_NAME).is_file():
            os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
            return


_prepare_path()

try:
    import mpv  # noqa: E402
except OSError as exc:  # pragma: no cover - depends on the machine
    raise LibmpvMissingError(
        f"Glancer could not load {LIBMPV_NAME}. Put it in the bin folder next to "
        "Glancer and start the app again."
    ) from exc

__all__ = ["mpv", "LibmpvMissingError"]
