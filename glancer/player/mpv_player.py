"""libmpv wrapper: loading, stepping, seeking, volume.

python-mpv delivers property changes and events on mpv's own event thread.
Those callbacks only post ``(name, value)`` to a ThreadBridge (no Qt calls
off the main thread), so every state change and every public signal is
handled on the Qt main thread.

Frame stepping uses ``frame-step ±1 seek``. The default "play" mode briefly
unpauses mpv, which (a) can leave playback running if it overlaps other seeks
and (b) drops steps when a held key sends ``pause`` mid-step. Seek mode never
touches the pause state, but mpv merges seeks issued before the previous one
finishes, so steps go through a queue: the next step is sent only after the
``playback-restart`` event of the previous one.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

from glancer.media_types import MediaKind, detect_kind, is_supported
from glancer.player.libmpv import mpv
from glancer.player.thread_bridge import ThreadBridge

# Never let a held key build up more than this many unsent steps; otherwise a
# slow backward step (long GOP) could keep stepping well after the key is released.
MAX_PENDING_STEPS = 8
# If mpv never reports the end of a step (e.g. stepping past the last frame),
# unblock the queue after this long.
STEP_WATCHDOG_MS = 1500

SPEEDS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


def _mpv_options(volume: int, muted: bool) -> dict[str, Any]:
    return {
        "vo": "libmpv",
        "hwdec": "auto-safe",
        "keep_open": "always",
        "idle": "yes",
        "pause": True,
        "image_display_duration": "inf",
        "audio_display": "embedded-first",
        "hr_seek": "yes",
        "hr_seek_framedrop": False,
        "background_color": "#000000",
        # Volume is applied before anything is loaded, so playback never
        # starts at a different level and then jumps.
        "volume": volume,
        "mute": muted,
        # No input handling, OSD, scripts or config of mpv's own.
        "input_default_bindings": False,
        "input_vo_keyboard": False,
        "osc": False,
        "osd_level": 0,
        "load_scripts": False,
        "ytdl": False,
        "config": False,
        # Privacy: never write watch-later/resume files or logs.
        "save_position_on_quit": False,
        "resume_playback": False,
        "terminal": False,
        "msg_level": "all=warn",
    }


class MpvPlayer(QObject):
    file_loaded = Signal(object, object)  # Path, MediaKind
    load_failed = Signal(str)
    duration_changed = Signal(float)
    position_changed = Signal(float)
    seek_started = Signal(float)  # absolute seek requested (target seconds)
    pause_changed = Signal(bool)
    volume_changed = Signal(int)
    mute_changed = Signal(bool)
    video_size_changed = Signal(int, int)  # display width/height after rotation
    step_finished = Signal()
    speed_changed = Signal(float)
    loop_changed = Signal(bool)
    rotation_changed = Signal(int)  # view rotation in degrees (0/90/180/270)
    metadata_changed = Signal(object)  # dict of lower-cased tag names

    def __init__(self, volume: int, muted: bool, parent: QObject | None = None):
        super().__init__(parent)
        self._closed = False
        self._path: Path | None = None
        self._kind: MediaKind | None = None
        self._duration = 0.0
        self._position = 0.0
        self._paused = True
        self._pending_steps = 0
        self._step_in_flight = False
        self._speed = 1.0
        self._loop_choice = False  # the user's loop setting for videos/audio
        self._loop = False
        self._rotation = 0
        self._volume = max(0, min(100, int(volume)))
        self._muted = bool(muted)
        self._view_transform = (0.0, 0.0, 0.0)  # video-zoom, video-pan-x, video-pan-y
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(STEP_WATCHDOG_MS)
        self._watchdog.timeout.connect(self._step_done)

        self._bridge: ThreadBridge[tuple[str, object]] = ThreadBridge(self._on_bridge_items, self)
        self._mpv = mpv.MPV(**_mpv_options(volume, muted))

        for name in ("duration", "time-pos", "pause", "volume", "mute", "video-params", "metadata"):
            self._mpv.observe_property(name, self._forwarder(name))

        @self._mpv.event_callback("playback-restart", "file-loaded", "end-file")
        def _on_event(event: Any) -> None:
            data = event.as_dict()
            name = data.get("event", b"")
            if isinstance(name, bytes):
                name = name.decode()
            reason = data.get("reason", b"")
            if isinstance(reason, bytes):
                reason = reason.decode()
            self._emit_bridge(f"event:{name}", reason)

        self._event_handler = _on_event

    # -- thread bridge --------------------------------------------------------

    def _emit_bridge(self, name: str, value: object) -> None:
        # Runs on mpv's event thread.
        if not self._closed:
            self._bridge.post((name, value))

    def _forwarder(self, name: str) -> Callable[[str, object], None]:
        def forward(_name: str, value: object) -> None:
            self._emit_bridge(name, value)

        return forward

    def _on_bridge_items(self, items: list[tuple[str, object]]) -> None:
        for name, value in items:
            if self._closed:
                return
            self._on_bridge(name, value)

    def _on_bridge(self, name: str, value: object) -> None:
        if name == "duration":
            self._duration = float(value or 0)
            self.duration_changed.emit(self._duration)
        elif name == "time-pos":
            if value is not None:
                self._position = float(value)
                self.position_changed.emit(self._position)
        elif name == "pause":
            self._paused = bool(value)
            self.pause_changed.emit(self._paused)
        elif name == "volume":
            if value is not None:
                self._volume = round(float(value))
                self.volume_changed.emit(self._volume)
        elif name == "mute":
            self._muted = bool(value)
            self.mute_changed.emit(self._muted)
        elif name == "video-params":
            if isinstance(value, dict) and value.get("dw") and value.get("dh"):
                width, height = int(value["dw"]), int(value["dh"])
                if int(value.get("rotate") or 0) % 180 == 90:
                    width, height = height, width
                self.video_size_changed.emit(width, height)
        elif name == "metadata":
            tags = value if isinstance(value, dict) else {}
            self.metadata_changed.emit({str(k).lower(): str(v) for k, v in tags.items()})
        elif name == "event:playback-restart":
            if self._step_in_flight:
                self._step_done()
        elif name == "event:file-loaded":
            if self._path is not None and self._kind is not None:
                self.file_loaded.emit(self._path, self._kind)
        elif name == "event:end-file" and value == "error":
            failed = self._path
            self._path = None
            self._kind = None
            label = failed.name if failed else "The file"
            self.load_failed.emit(f"{label} could not be opened. It may be damaged.")

    # -- state ----------------------------------------------------------------

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def kind(self) -> MediaKind | None:
        return self._kind

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def position(self) -> float:
        return self._position

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def pending_steps(self) -> int:
        return self._pending_steps + (1 if self._step_in_flight else 0)

    def create_render_context(self, get_proc_address: Any) -> Any:
        return mpv.MpvRenderContext(
            self._mpv,
            "opengl",
            opengl_init_params={"get_proc_address": get_proc_address},
        )

    def get_property(self, name: str) -> Any:
        return self._mpv._get_property(name)

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def loop(self) -> bool:
        return self._loop

    @property
    def rotation(self) -> int:
        return self._rotation

    def has_video_track(self) -> bool:
        """True if the loaded file has a picture (for audio: cover art)."""
        tracks = self._mpv._get_property("track-list") or []
        return any(t.get("type") == "video" for t in tracks)

    # -- loading --------------------------------------------------------------

    def load(self, path: Path) -> bool:
        """Open a file. Returns False (and emits load_failed) if unsupported."""
        if not path.is_file():
            self.load_failed.emit(f"{path.name} no longer exists.")
            return False
        if not is_supported(path):
            suffix = path.suffix or "this type of"
            self.load_failed.emit(f"Glancer can't open {suffix} files.")
            return False

        kind = detect_kind(path)
        self._cancel_steps()
        self._path = path
        self._kind = kind
        self._duration = 0.0
        self._position = 0.0
        # Animations always start looping; videos and audio keep the user's choice.
        self._apply_loop(kind is MediaKind.ANIMATED or self._loop_choice)
        self.set_rotation(0)  # rotation is a per-file view setting
        # Videos, audio and animations start playing, like other media players.
        self._mpv.pause = kind is MediaKind.IMAGE
        self.set_limits(end=None, loop=None)  # an editor's trim range never carries over
        self._mpv.loadfile(str(path), "replace")
        return True

    def unload(self) -> None:
        self._cancel_steps()
        self._path = None
        self._kind = None
        self._mpv.command("stop")

    # -- playback -------------------------------------------------------------

    def _set(self, name: str, value: object) -> None:
        """Set an mpv property without waiting for mpv's core.

        A synchronous set blocks this (the UI) thread until the core thread is
        free. While paused, the core redraws after zoom/pan/etc. changes and
        waits for this thread to render that frame, so the two wait on each
        other until mpv's timeout: ~25 ms per set in fullscreen (dropping a
        paused, zoomed drag to ~10 fps). Async commands run in order, so
        later commands still see the new value.
        """
        text = ("yes" if value else "no") if isinstance(value, bool) else str(value)
        self._mpv.command_async("set", name, text)

    def set_limits(self, end: float | None, loop: tuple[float, float] | None) -> None:
        """Stop at `end` (seconds) and/or loop between loop=(a, b); None clears."""
        self._set("end", "none" if end is None else f"{end:.6f}")
        self._set("ab-loop-a", "no" if loop is None else f"{loop[0]:.6f}")
        self._set("ab-loop-b", "no" if loop is None else f"{loop[1]:.6f}")

    def toggle_pause(self) -> None:
        if self._kind is not None and self._kind.has_timeline:
            self.set_paused(not self._paused)

    def set_paused(self, paused: bool) -> None:
        # Record the request now: the confirmation from mpv arrives a moment later,
        # and a quick second toggle (e.g. a double-click) must see this one.
        self._paused = paused
        self._set("pause", paused)

    def seek(self, seconds: float, exact: bool = True) -> None:
        """Seek to a time. exact=False lands on a nearby keyframe (fast scrubbing)."""
        if self._kind is None or not self._kind.has_timeline:
            return
        self._cancel_steps()
        mode = "absolute+exact" if exact else "absolute+keyframes"
        self.seek_started.emit(float(seconds))
        self._mpv.command_async("seek", max(0.0, seconds), mode)

    def seek_relative(self, delta: float) -> None:
        if self._kind is None or not self._kind.has_timeline:
            return
        self._cancel_steps()
        self._mpv.command_async("seek", delta, "relative+exact")

    def step(self, frames: int) -> None:
        """Queue a frame step (+1 forward, -1 back). Pauses playback."""
        if self._kind is None or not self._kind.has_frames or frames == 0:
            return
        if not self._paused:
            self._set("pause", True)
        self._pending_steps = max(
            -MAX_PENDING_STEPS, min(MAX_PENDING_STEPS, self._pending_steps + frames)
        )
        self._dispatch_step()

    def _dispatch_step(self) -> None:
        if self._step_in_flight or self._pending_steps == 0:
            return
        direction = 1 if self._pending_steps > 0 else -1
        self._pending_steps -= direction
        self._step_in_flight = True
        self._watchdog.start()
        self._mpv.command_async("frame-step", direction, "seek")

    def _step_done(self) -> None:
        self._watchdog.stop()
        self._step_in_flight = False
        # The observed time-pos update can arrive ~50 ms after
        # playback-restart; read it now so position is never a frame stale.
        position = self._mpv.time_pos
        if position is not None and position != self._position:
            self._position = float(position)
            self.position_changed.emit(self._position)
        if self._pending_steps:
            self._dispatch_step()
        else:
            self.step_finished.emit()

    def _cancel_steps(self) -> None:
        self._pending_steps = 0
        self._step_in_flight = False
        self._watchdog.stop()

    # -- speed, loop, rotation -------------------------------------------------

    def set_speed(self, speed: float) -> None:
        self._speed = max(SPEEDS[0], min(SPEEDS[-1], float(speed)))
        self._set("speed", self._speed)
        self.speed_changed.emit(self._speed)

    def change_speed(self, direction: int) -> None:
        """Move to the next faster (+1) or slower (-1) preset speed."""
        if direction > 0:
            faster = [s for s in SPEEDS if s > self._speed + 1e-6]
            if faster:
                self.set_speed(faster[0])
        else:
            slower = [s for s in SPEEDS if s < self._speed - 1e-6]
            if slower:
                self.set_speed(slower[-1])

    def set_loop(self, loop: bool) -> None:
        if self._kind is not MediaKind.ANIMATED:
            self._loop_choice = loop
        self._apply_loop(loop)

    def _apply_loop(self, loop: bool) -> None:
        self._loop = loop
        self._set("loop-file", "inf" if loop else "no")
        self.loop_changed.emit(loop)

    def set_rotation(self, degrees: int) -> None:
        self._rotation = degrees % 360
        self._set("video-rotate", self._rotation)
        self.rotation_changed.emit(self._rotation)

    def rotate(self, degrees: int) -> None:
        self.set_rotation(self._rotation + degrees)

    def frame_number(self) -> int:
        return int(self._mpv._get_property("estimated-frame-number") or 0)

    def save_frame(self, path: Path) -> None:
        """Write the current frame at its original resolution, without overlays."""
        self._mpv.command("screenshot-to-file", str(path), "video")

    def set_view_transform(self, zoom_log2: float, pan_x: float, pan_y: float) -> None:
        """mpv's video-zoom (log2 scale) and video-pan (fractions of the shown size)."""
        values = (zoom_log2, pan_x, pan_y)
        if values == self._view_transform:
            return
        for name, old, new in zip(("video-zoom", "video-pan-x", "video-pan-y"),
                                  self._view_transform, values):
            if old != new:
                self._set(name, new)
        self._view_transform = values

    def set_video_margins(self, left: float, top: float, right: float, bottom: float) -> None:
        """Reserve fractions of the view so the picture is drawn inside the rest."""
        for side, value in zip(("left", "top", "right", "bottom"), (left, top, right, bottom)):
            self._set(f"video-margin-ratio-{side}", value)

    # -- volume ---------------------------------------------------------------

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, int(volume)))
        self._set("volume", self._volume)

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._set("mute", self._muted)

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def muted(self) -> bool:
        return self._muted

    # -- shutdown -------------------------------------------------------------

    def close(self) -> None:
        """Stop and destroy the mpv instance. Free the view's render context first.

        mpv is destroyed on a worker thread, not the Qt main thread. If mpv had
        played audio (WASAPI) and was destroyed on Qt's main thread, which runs
        COM in single-threaded mode, QApplication's destructor later crashed
        at exit every time. Destroying it from a plain worker thread avoids that.
        """
        if self._closed:
            return
        self._closed = True
        self._watchdog.stop()
        self._bridge.close()
        worker = threading.Thread(target=self._mpv.terminate, name="mpv-terminate")
        worker.start()
        worker.join(timeout=5)
