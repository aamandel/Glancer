from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket


class MpvController(QObject):
    connected = Signal()
    property_changed = Signal(str, object)
    playback_error = Signal(str)
    stopped = Signal()

    def __init__(self, mpv_path: Path, window_id: int, parent: QObject | None = None):
        super().__init__(parent)
        self._mpv_path = mpv_path
        self._window_id = window_id
        self._pipe_name = rf"\\.\pipe\glancer-{os.getpid()}-{uuid.uuid4().hex}"
        self._process: subprocess.Popen[bytes] | None = None
        self._socket = QLocalSocket(self)
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(50)
        self._connect_deadline = 0.0
        self._pending_commands: list[dict[str, Any]] = []
        self._read_buffer = b""
        self._closing = False
        self._ever_connected = False
        self._connection_error_reported = False
        self._next_request_id = 1
        self._pending_responses: set[int] = set()

        self._socket.connected.connect(self._socket_connected)
        self._socket.readyRead.connect(self._read_messages)
        self._socket.disconnected.connect(self._socket_disconnected)
        self._socket.errorOccurred.connect(self._socket_error)
        self._retry_timer.timeout.connect(self._try_connect)

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def pending_response_count(self) -> int:
        return len(self._pending_responses)

    def start(self) -> None:
        if self.is_running:
            return

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        arguments = [
            str(self._mpv_path),
            f"--wid={self._window_id}",
            f"--input-ipc-server={self._pipe_name}",
            "--idle=yes",
            "--force-window=immediate",
            "--keep-open=yes",
            "--pause=yes",
            "--osc=no",
            "--input-default-bindings=no",
            "--input-cursor=no",
            "--hr-seek-framedrop=no",
            "--terminal=no",
            "--msg-level=all=warn",
        ]
        try:
            self._process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        except OSError as exc:
            self.playback_error.emit(f"Could not start mpv:\n{exc}")
            return

        self._connect_deadline = time.monotonic() + 8
        self._retry_timer.start()
        self._try_connect()

    def _try_connect(self) -> None:
        if self._closing or self._ever_connected:
            self._retry_timer.stop()
            return
        if not self.is_running:
            self._retry_timer.stop()
            self.playback_error.emit("mpv stopped before the player could connect.")
            self.stopped.emit()
            return
        if time.monotonic() >= self._connect_deadline:
            self._retry_timer.stop()
            self.playback_error.emit("Timed out while connecting to mpv.")
            return
        if self._socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._socket.connectToServer(self._pipe_name)

    def _socket_connected(self) -> None:
        self._retry_timer.stop()
        self._ever_connected = True
        self._observe_properties()
        pending, self._pending_commands = self._pending_commands, []
        for payload in pending:
            self._write(payload)
        self.connected.emit()

    def _socket_error(self, _error: QLocalSocket.LocalSocketError) -> None:
        if self._closing:
            return
        if not self._ever_connected:
            # mpv normally needs several connection attempts while starting.
            return
        if not self._connection_error_reported:
            self._connection_error_reported = True
            self.playback_error.emit(
                f"Lost the connection to mpv:\n{self._socket.errorString()}"
            )

    def _socket_disconnected(self) -> None:
        if not self._closing and self._ever_connected:
            self.stopped.emit()

    def _read_messages(self) -> None:
        self._read_buffer += bytes(self._socket.readAll())
        while b"\n" in self._read_buffer:
            line, self._read_buffer = self._read_buffer.split(b"\n", 1)
            self._handle_message(line)

    def _handle_message(self, raw_message: bytes) -> None:
        try:
            message = json.loads(raw_message)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        request_id = message.get("request_id")
        if isinstance(request_id, int):
            self._pending_responses.discard(request_id)
        if message.get("event") == "property-change":
            self.property_changed.emit(message.get("name", ""), message.get("data"))

    def _observe_properties(self) -> None:
        for observer_id, name in enumerate(
            ("time-pos", "duration", "pause", "path", "media-title"), start=1
        ):
            self._write({"command": ["observe_property", observer_id, name]})

    def _write(self, payload: dict[str, Any]) -> None:
        if self._socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
            self._pending_commands.append(payload)
            return
        payload = dict(payload)
        request_id = self._next_request_id
        self._next_request_id += 1
        payload["request_id"] = request_id
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        if self._socket.write(encoded) >= 0:
            self._pending_responses.add(request_id)
        elif not self._closing:
            self.playback_error.emit(
                f"Could not send a command to mpv:\n{self._socket.errorString()}"
            )
        self._socket.flush()

    def command(self, *parts: Any) -> None:
        self._write({"command": list(parts)})

    def load(self, path: Path) -> None:
        self.command("loadfile", str(path), "replace")
        self.command("set_property", "pause", True)

    def toggle_pause(self) -> None:
        self.command("cycle", "pause")

    def set_paused(self, paused: bool) -> None:
        self.command("set_property", "pause", paused)

    def seek(self, seconds: float) -> None:
        self.command("seek", seconds, "absolute", "exact")

    # "seek" mode steps by seeking instead of briefly unpausing to play one
    # frame. The default "play" mode can leave mpv playing after a burst of
    # steps (e.g. a held arrow key).
    def step_forward(self) -> None:
        self.command("frame-step", 1, "seek")

    def step_backward(self) -> None:
        self.command("frame-step", -1, "seek")

    def close(self) -> None:
        self._closing = True
        self._retry_timer.stop()
        if self.is_running:
            self.command("quit")
            self._socket.waitForBytesWritten(500)
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.terminate()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=2)
        self._socket.abort()
        self._process = None
