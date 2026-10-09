"""i3 workspaces over the IPC socket, in-process: no i3-msg/jq per event.

Fabric's own I3 service subscribes to every event (each window title change wakes it), so this
subscribes to workspace and output only.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
from typing import Any

from gi.repository import GLib

from services import mock
from services.state import State

HEADER = struct.Struct("<6sII")  # "i3-ipc", payload length, message type
GET_WORKSPACES, SUBSCRIBE = 1, 2
KEYS = ("name", "focused", "visible", "urgent", "output")


def message(kind: int, payload: bytes = b"") -> bytes:
    return HEADER.pack(b"i3-ipc", len(payload), kind) + payload


def receive(sock: socket.socket) -> bytes:
    """One reply or event body; struct.error when i3 closed the socket."""
    _, length, _ = HEADER.unpack(sock.recv(HEADER.size, socket.MSG_WAITALL))
    return sock.recv(length, socket.MSG_WAITALL)


def socket_path() -> str:
    return os.environ.get("I3SOCK") or subprocess.run(["i3", "--get-socketpath"], capture_output=True, text=True).stdout.strip()


class Workspaces(State):
    """value: [{name, focused, visible, urgent, output}], again on every workspace/output event.
    Reconnects every 2 s while i3 is gone (i3 restart)."""

    def __init__(self):
        super().__init__([])
        self.sockets: list[socket.socket] = []
        if mock.ENABLED:
            self.emit(mock.json_for("workspaces"))
            return
        self.connect()

    def connect(self) -> bool:
        try:
            path = socket_path()
            self.sockets = [socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) for _ in range(2)]
            for sock in self.sockets:
                sock.settimeout(1)  # i3 answers at once; never hang the main loop on it
                sock.connect(path)
            events, _ = self.sockets
            events.sendall(message(SUBSCRIBE, b'["workspace", "output"]'))
            receive(events)  # {"success": true}
            self.refresh()
        except (OSError, struct.error, ValueError):
            return self.reconnect()
        GLib.io_add_watch(self.sockets[0].fileno(), GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN | GLib.IOCondition.HUP, self.on_event)
        return False

    def reconnect(self) -> bool:
        for sock in self.sockets:
            sock.close()
        GLib.timeout_add_seconds(2, self.connect)
        return False

    def on_event(self, *_: Any) -> bool:
        try:
            receive(self.sockets[0])  # the body only says what changed; the list is read whole
            self.refresh()
        except (OSError, struct.error, ValueError):
            return self.reconnect()
        return True

    def refresh(self) -> None:
        query = self.sockets[1]
        query.sendall(message(GET_WORKSPACES))
        self.emit([{key: workspace.get(key) for key in KEYS} for workspace in json.loads(receive(query))])
