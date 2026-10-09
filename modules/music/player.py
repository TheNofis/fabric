"""MPRIS over D-Bus, in-process: the player the popup shows and its controls.

Event-driven while the popup is open: PropertiesChanged/Seeked from players, NameOwnerChanged for
players coming and going, and bluez's MediaPlayer1 for the iPhone (mpris-proxy). MPRIS doesn't signal
the position while playing, the window extrapolates it; a slow resync catches players that don't
signal a seek.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from gi.repository import Gio, GLib

from modules.music.logic import clock
from services import mock
from services.state import State

PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
PIN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "fabric-music-player")  # the source button's choice
TIMEOUT = 300  # ms per call; ponytail: sync calls, a hung player stalls the shell this long, go async if one does
RESYNC = 5  # s
IDLE = {"status": "Stopped", "title": "No media player", "artist": "Start a player to see track details", "art": "",
        "position": 0, "length": 1, "duration": "0:00", "source": "", "phone": False, "players": 0}


class Player(State):
    def __init__(self):
        super().__init__(IDLE)
        self.session = self.system = None
        self.subscriptions: list[tuple[Gio.DBusConnection, int]] = []
        self.pending = self.resync = 0
        self.player = ""

    def start(self) -> None:
        if mock.ENABLED:
            self.emit(mock.json_for("music"))
            return
        if self.subscriptions:
            return
        try:
            self.session = self.session or Gio.bus_get_sync(Gio.BusType.SESSION)
            self.system = self.system or Gio.bus_get_sync(Gio.BusType.SYSTEM)
        except GLib.Error:
            return
        changed = lambda *_: self.soon()
        self.subscriptions = [
            (self.session, self.session.signal_subscribe(None, "org.freedesktop.DBus.Properties", "PropertiesChanged", PATH, None, Gio.DBusSignalFlags.NONE, changed)),
            (self.session, self.session.signal_subscribe(None, PLAYER, "Seeked", PATH, None, Gio.DBusSignalFlags.NONE, changed)),
            (self.session, self.session.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged", None,
                                                         PREFIX.rstrip("."), Gio.DBusSignalFlags.MATCH_ARG0_NAMESPACE, changed)),
            (self.system, self.system.signal_subscribe("org.bluez", "org.freedesktop.DBus.Properties", "PropertiesChanged", None,
                                                       "org.bluez.MediaPlayer1", Gio.DBusSignalFlags.NONE, changed)),
        ]
        self.resync = GLib.timeout_add_seconds(RESYNC, lambda: self.refresh() or True)
        self.refresh()

    def stop(self) -> None:
        for bus, subscription in self.subscriptions:
            bus.signal_unsubscribe(subscription)
        self.subscriptions = []
        for source in (self.pending, self.resync):
            if source:
                GLib.source_remove(source)
        self.pending = self.resync = 0

    def soon(self) -> None:
        # a track change is a burst of signals: read once after it
        if not self.pending:
            self.pending = GLib.idle_add(self.refresh)

    def call(self, bus: Any, name: str, path: str, interface: str, method: str, args: GLib.Variant | None = None) -> Any:
        try:
            return bus.call_sync(name, path, interface, method, args, None, Gio.DBusCallFlags.NONE, TIMEOUT, None).unpack()
        except GLib.Error:
            return None

    def get(self, player: str, interface: str, prop: str) -> Any:
        reply = self.call(self.session, player, PATH, "org.freedesktop.DBus.Properties", "Get", GLib.Variant("(ss)", (interface, prop)))
        return reply[0] if reply else None

    def players(self) -> list[str]:
        names = self.call(self.session, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames")
        return sorted(name for name in (names[0] if names else []) if name.startswith(PREFIX))

    def pick(self, players: list[str]) -> str:
        """playing > pinned (the source button) > paused > first; the iPhone (mpris-proxy) idles as Stopped."""
        if len(players) < 2:
            return players[0] if players else ""
        statuses = {player: self.get(player, PLAYER, "PlaybackStatus") for player in players}
        try:
            pinned = PIN.read_text().strip()
        except OSError:
            pinned = ""
        return (next((p for p in players if statuses[p] == "Playing"), None)
                or (pinned if pinned in players else None)
                or next((p for p in players if statuses[p] == "Paused"), players[0]))

    def is_phone(self, player: str) -> bool:
        """mpris-proxy bridges a Bluetooth source (the iPhone's AVRCP) onto MPRIS."""
        pid = self.call(self.session, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                        "GetConnectionUnixProcessID", GLib.Variant("(s)", (player,)))
        try:
            return bool(pid) and Path(f"/proc/{pid[0]}/comm").read_text().strip() == "mpris-proxy"
        except OSError:
            return False

    def phone_position(self) -> int | None:
        """mpris-proxy caches the iPhone's position from the last play/pause/seek; bluez runs the live clock (ms).
        ponytail: first bluez player wins, pick by device if two phones ever stream at once."""
        objects = self.call(self.system, "org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
        for interfaces in (objects[0] if objects else {}).values():
            if "Position" in (media := interfaces.get("org.bluez.MediaPlayer1", {})):
                return int(media["Position"]) * 1000
        return None

    def refresh(self) -> bool:
        self.pending = 0
        players = self.players()
        self.player = self.pick(players)
        if not self.player:
            self.emit(IDLE)
            return False
        props = self.call(self.session, self.player, PATH, "org.freedesktop.DBus.Properties", "GetAll", GLib.Variant("(s)", (PLAYER,)))
        props = props[0] if props else {}
        meta = props.get("Metadata") or {}
        phone = self.is_phone(self.player)
        live = self.phone_position() if phone else None
        position = live if live is not None else int(props.get("Position") or 0)
        length = int(meta.get("mpris:length") or 1)
        self.emit({
            "status": props.get("PlaybackStatus") or "Stopped",
            "title": meta.get("xesam:title") or "Unknown track",
            "artist": (meta.get("xesam:artist") or [""])[0] or "Unknown artist",
            "art": meta.get("mpris:artUrl") or "",
            "position": position,
            "length": length,
            "duration": clock(length),
            "source": self.get(self.player, "org.mpris.MediaPlayer2", "Identity") or "",
            "phone": phone,
            "players": len(players),
        })
        return False

    def command(self, method: str) -> None:
        if not mock.ENABLED and self.player and self.session:
            self.session.call(self.player, PATH, PLAYER, method, None, None, Gio.DBusCallFlags.NONE, TIMEOUT, None)

    def play_pause(self) -> None:
        self.command("PlayPause")

    def previous(self) -> None:
        self.command("Previous")

    def next(self) -> None:
        self.command("Next")

    def switch(self) -> None:
        """Hand off: pause what plays here, pin the next source so it shows up ready to play."""
        players = self.players()
        if self.player not in players or len(players) < 2:
            return
        following = players[(players.index(self.player) + 1) % len(players)]
        if self.get(self.player, PLAYER, "PlaybackStatus") == "Playing":
            self.command("Pause")
        PIN.write_text(following)
        self.soon()
