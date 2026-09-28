"""In-process polling sources: clock, CPU/RAM/temperature, network, keyboard."""

from __future__ import annotations

import ctypes
import ctypes.util
import fcntl
import socket
import struct
from datetime import datetime
from pathlib import Path
from typing import Any

from gi.repository import GLib

from services.state import PollingState, State


class ClockState(PollingState):
    # Every consumer shows minutes at most, so the value (and callbacks) change once per minute.
    def read(self) -> datetime:
        return datetime.now().replace(second=0, microsecond=0)


class SystemState(PollingState):
    def __init__(self):
        self.previous_cpu: tuple[int, int] | None = None
        self.sensors = self.find_sensors()
        super().__init__({"cpu": 0.0, "used": 0.0, "total": 0.0, "temp": 0.0})

    @staticmethod
    def cpu() -> tuple[int, int]:
        values = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        return sum(values), values[3] + (values[4] if len(values) > 4 else 0)

    @staticmethod
    def memory() -> tuple[float, float]:
        fields = {
            key.rstrip(":"): int(value)
            for key, value, *_ in (line.split() for line in Path("/proc/meminfo").read_text().splitlines())
        }
        total = fields["MemTotal"] * 1024
        return total - fields.get("MemAvailable", fields["MemFree"]) * 1024, total

    @staticmethod
    def find_sensors() -> list[Path]:
        preferred: list[Path] = []
        fallback: list[Path] = []
        for value_file in Path("/sys/class/hwmon").glob("hwmon*/temp*_input"):
            label_file = value_file.with_name(value_file.name.replace("_input", "_label"))
            try:
                label = label_file.read_text().strip().lower()
            except OSError:
                label = ""
            (preferred if "package" in label else fallback).append(value_file)
        return preferred or fallback

    def temperature(self) -> float:
        values = []
        for value_file in self.sensors:
            try:
                values.append(float(value_file.read_text()) / 1000)
            except (OSError, ValueError):
                pass
        return max(values, default=0.0)

    def read(self) -> dict[str, float]:
        current = self.cpu()
        cpu = 0.0
        if self.previous_cpu:
            total = current[0] - self.previous_cpu[0]
            idle = current[1] - self.previous_cpu[1]
            cpu = 100 * (total - idle) / total if total else 0.0
        self.previous_cpu = current
        used, total_memory = self.memory()
        return {"cpu": cpu, "used": used, "total": total_memory, "temp": self.temperature()}


def human_bytes(value: float) -> str:
    units = "BKMG"
    index = 0
    while value >= 1000 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{value:.1f}{units[index]}" if index and value < 10 else f"{int(value)}{units[index]}"


def default_interface(route_table: str) -> str:
    """Interface of the lowest-metric default route in /proc/net/route format."""
    best: tuple[int, str] | None = None
    for line in route_table.splitlines()[1:]:
        fields = line.split()
        if len(fields) > 6 and fields[1] == "00000000" and int(fields[3], 16) & 1:
            candidate = (int(fields[6]), fields[0])
            best = min(best, candidate) if best else candidate
    return best[1] if best else ""


class NetworkState(PollingState):
    """Default-route interface, IPv4 and per-second traffic, read in-process."""

    SIOCGIFADDR = 0x8915

    def __init__(self):
        self.counters: tuple[str, int, int] | None = None
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        super().__init__({"iface": "", "ip": "offline", "down": "", "up": ""})

    def address(self, iface: str) -> str:
        try:
            request = struct.pack("256s", iface[:15].encode())
            return socket.inet_ntoa(fcntl.ioctl(self.socket.fileno(), self.SIOCGIFADDR, request)[20:24])
        except OSError:
            return ""

    def read(self) -> dict[str, str]:
        try:
            iface = default_interface(Path("/proc/net/route").read_text())
            stats = Path("/sys/class/net", iface, "statistics") if iface else None
            rx = int((stats / "rx_bytes").read_text()) if stats else 0
            tx = int((stats / "tx_bytes").read_text()) if stats else 0
        except (OSError, ValueError):
            iface, rx, tx = "", 0, 0
        if not iface:
            self.counters = None
            return {"iface": "", "ip": "offline", "down": "", "up": ""}
        # a new interface resets the baseline, otherwise the delta is garbage
        _, rx0, tx0 = self.counters if self.counters and self.counters[0] == iface else (iface, rx, tx)
        self.counters = (iface, rx, tx)
        return {
            "iface": iface,
            "ip": self.address(iface),
            "down": human_bytes(max(rx - rx0, 0)),
            "up": human_bytes(max(tx - tx0, 0)),
        }


class XkbStateRec(ctypes.Structure):
    _fields_ = [
        ("group", ctypes.c_ubyte),
        ("locked_group", ctypes.c_ubyte),
        ("base_group", ctypes.c_ushort),
        ("latched_group", ctypes.c_ushort),
        ("mods", ctypes.c_ubyte),
        ("base_mods", ctypes.c_ubyte),
        ("latched_mods", ctypes.c_ubyte),
        ("locked_mods", ctypes.c_ubyte),
        ("compat_state", ctypes.c_ubyte),
        ("grab_mods", ctypes.c_ubyte),
        ("compat_grab_mods", ctypes.c_ubyte),
        ("lookup_mods", ctypes.c_ubyte),
        ("compat_lookup_mods", ctypes.c_ubyte),
        ("ptr_buttons", ctypes.c_ushort),
    ]


class KeyboardState(State):
    """XKB layout group and caps lock, pushed by XkbStateNotify events on a private X connection."""

    LAYOUTS = ("us", "ru")  # index = xkb group
    XKB_USE_CORE_KBD = 0x100
    XKB_STATE_NOTIFY = 2
    XKB_STATE_DETAILS = (1 << 4) | (1 << 3)  # XkbGroupStateMask | XkbModifierLockMask
    LOCK_MASK = 1 << 1

    def __init__(self):
        xlib = self.xlib = ctypes.CDLL(ctypes.util.find_library("X11"))
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XkbGetState.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(XkbStateRec))
        xlib.XkbSelectEventDetails.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_ulong)
        xlib.XNextEvent.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        xlib.XPending.argtypes = xlib.XFlush.argtypes = xlib.XConnectionNumber.argtypes = (ctypes.c_void_p,)
        self.display = xlib.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("Cannot open X display for keyboard state")
        self.state = XkbStateRec()
        self.event = ctypes.create_string_buffer(192)  # sizeof(XEvent)
        super().__init__({"layout": self.LAYOUTS[0], "caps": False})
        xlib.XkbSelectEventDetails(self.display, self.XKB_USE_CORE_KBD, self.XKB_STATE_NOTIFY, self.XKB_STATE_DETAILS, self.XKB_STATE_DETAILS)
        xlib.XFlush(self.display)
        self.emit(self.read())
        GLib.io_add_watch(xlib.XConnectionNumber(self.display), GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN, self.on_events)

    def on_events(self, *_: Any) -> bool:
        # the event only says "changed": drain the queue, then read the state once
        while self.xlib.XPending(self.display):
            while self.xlib.XPending(self.display):
                self.xlib.XNextEvent(self.display, self.event)
            self.emit(self.read())
        return True

    def read(self) -> dict[str, Any]:
        if self.xlib.XkbGetState(self.display, self.XKB_USE_CORE_KBD, ctypes.byref(self.state)) != 0:
            return self.value
        group = self.state.group
        return {
            "layout": self.LAYOUTS[group] if group < len(self.LAYOUTS) else str(group + 1),
            "caps": bool(self.state.locked_mods & self.LOCK_MASK),
        }
