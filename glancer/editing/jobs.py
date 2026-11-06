"""Background ffmpeg jobs, one at a time, with progress and cancel.

    QProcess runs ffmpeg with `-progress pipe:1`; ffmpeg writes lines like
    "out_time_us=1234567", so progress = out_time / expected duration.
    Cancel kills ffmpeg and deletes the partial output.
    Further jobs wait in a queue.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QProcess, Signal

STDERR_TAIL_LINES = 6


@dataclass(eq=False)
class Job:
    command: list[str]
    output: Path
    duration: float  # expected output length in seconds (for progress)
    label: str
    # Runs on success before `finished` (e.g. the overwrite swap). It may raise
    # an exception whose message becomes the job's error.
    on_success: Callable[[], None] | None = None
    error: str = field(default="", init=False)


class JobRunner(QObject):
    started = Signal(object)  # Job
    progress = Signal(object, float)  # Job, 0..1
    finished = Signal(object, bool)  # Job, succeeded (job.error says why not)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._queue: deque[Job] = deque()
        self._current: Job | None = None
        self._process: QProcess | None = None
        self._stderr: list[str] = []
        self._cancelled = False

    @property
    def busy(self) -> bool:
        return self._current is not None or bool(self._queue)

    @property
    def current(self) -> Job | None:
        return self._current

    def submit(self, job: Job) -> None:
        self._queue.append(job)
        if self._current is None:
            self._start_next()

    def cancel_current(self) -> None:
        if self._process is not None and self._current is not None:
            self._cancelled = True
            self._process.kill()
            self._process.waitForFinished(3000)

    def cancel_all(self) -> None:
        self._queue.clear()
        self.cancel_current()

    def _start_next(self) -> None:
        if not self._queue:
            return
        job = self._queue.popleft()
        self._current = job
        self._cancelled = False
        self._stderr = []
        process = QProcess(self)
        process.setProgram(job.command[0])
        process.setArguments(job.command[1:])
        process.readyReadStandardOutput.connect(self._read_progress)
        process.readyReadStandardError.connect(self._read_stderr)
        process.finished.connect(self._process_finished)
        process.errorOccurred.connect(self._process_error)
        self._process = process
        self.started.emit(job)
        process.start()

    def _read_progress(self) -> None:
        job, process = self._current, self._process
        if job is None or process is None:
            return
        for raw in bytes(process.readAllStandardOutput().data()).decode(errors="replace").split():
            key, _, value = raw.partition("=")
            if key == "out_time_us" and value.lstrip("-").isdigit() and job.duration > 0:
                fraction = int(value) / 1_000_000 / job.duration
                self.progress.emit(job, max(0.0, min(1.0, fraction)))

    def _read_stderr(self) -> None:
        if self._process is not None:
            text = bytes(self._process.readAllStandardError().data()).decode(errors="replace")
            self._stderr = (self._stderr + text.splitlines())[-STDERR_TAIL_LINES:]

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._complete(False, "ffmpeg could not be started.")

    def _process_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        if self._current is None:
            return
        if self._cancelled:
            self._complete(False, "Cancelled.")
        elif status == QProcess.ExitStatus.NormalExit and exit_code == 0:
            self._complete(True, "")
        else:
            detail = self._stderr[-1] if self._stderr else f"exit code {exit_code}"
            self._complete(False, f"ffmpeg failed: {detail}")

    def _complete(self, ok: bool, error: str) -> None:
        job = self._current
        if job is None:
            return
        if ok and job.on_success is not None:
            try:
                job.on_success()
            except Exception as exc:  # the swap failed: the original is untouched
                ok, error = False, str(exc) or type(exc).__name__
        if not ok:
            job.output.unlink(missing_ok=True)  # never leave partial output behind
        job.error = error
        self._current = None
        if self._process is not None:
            self._process.deleteLater()
            self._process = None
        self.finished.emit(job, ok)
        self._start_next()
