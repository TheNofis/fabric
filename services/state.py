"""State base classes: State, PollingState and the script-backed JsonState."""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from typing import Any, Callable

from gi.repository import Gio, GLib


def parse_json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


class State:
    """Observable value: a subscriber gets the current value, then every change.

    Every data source extends this and only decides *when* to call emit().
    """

    def __init__(self, default: Any = None):
        self.value = default
        self.callbacks: list[Callable[[Any], None]] = []

    def subscribe(self, callback: Callable[[Any], None]) -> None:
        self.callbacks.append(callback)
        callback(self.value)

    def emit(self, value: Any) -> None:
        if value == self.value:
            return
        self.value = value
        for callback in self.callbacks:
            callback(value)


class PollingState(State):
    """State refreshed by a GLib timer: subclasses set `interval` (ms) and implement read()."""

    interval = 1000

    def __init__(self, default: Any = None):
        super().__init__(default)
        self.tick()
        GLib.timeout_add(self.interval, self.tick)

    def read(self) -> Any:
        raise NotImplementedError

    def tick(self) -> bool:
        self.emit(self.read())
        return True


class JsonState(State):
    """Line-delimited JSON from a long-running script.

    The script runs in its own process group (setsid) so the whole pipeline,
    including `pactl subscribe`/`i3-msg subscribe`, is killed on stop. stderr
    is discarded: an unread stderr pipe fills up and freezes the script. The
    script is respawned when it exits (i3 restart, pipewire restart, ...).
    """

    instances: list[JsonState] = []

    def __init__(self, script: Path, default: Any, autostart: bool = True):
        super().__init__(default)
        self.script = str(script)
        self.process: Gio.Subprocess | None = None
        self.running = False
        self.respawn = 0
        JsonState.instances.append(self)
        if autostart:
            self.start()

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._spawn()

    def stop(self) -> None:
        self.running = False
        if self.respawn:
            GLib.source_remove(self.respawn)
            self.respawn = 0
        if self.process and (pid := self.process.get_identifier()):
            try:
                os.killpg(int(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        self.process = None

    @classmethod
    def stop_all(cls) -> None:
        for state in cls.instances:
            state.stop()

    def _spawn(self) -> bool:
        self.respawn = 0
        if not self.running:
            return False
        process = Gio.Subprocess.new(
            ["setsid", self.script],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
        )
        self.process = process
        stream = Gio.DataInputStream(base_stream=process.get_stdout_pipe(), close_base_stream=True)
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._line, process)
        return False

    def _line(self, stream: Gio.DataInputStream, result: Gio.AsyncResult, process: Gio.Subprocess) -> None:
        try:
            line, _ = stream.read_line_finish(result)
        except GLib.Error:
            line = None
        if process is not self.process:
            stream.close()  # stale reader of a stopped process (GSubprocess reaps it)
            return
        if line is None:
            stream.close()
            self.process = None
            if self.running:
                self.respawn = GLib.timeout_add_seconds(2, self._spawn)
            return
        self._changed(line.decode(errors="replace"))
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._line, process)

    def _changed(self, raw: str) -> None:
        self.emit(parse_json(raw, self.value))
