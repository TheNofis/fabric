"""In-process polling sources: clock, CPU/RAM/temperature, network, backlight, keyboard."""

from __future__ import annotations

import ctypes
import ctypes.util
import fcntl
import os
import socket
import struct
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from gi.repository import GLib

from services.state import PollingState, State
from services import mock
from shared.widgets import run


class ClockState(PollingState):
    # Every consumer shows minutes at most, so the value (and callbacks) change once per minute.
    def read(self) -> datetime:
        return mock.NOW if mock.ENABLED else datetime.now().replace(second=0, microsecond=0)


def busy(now: tuple[int, int], before: tuple[int, int]) -> float:
    """Busy percent between two (total, idle) jiffy samples of a /proc/stat cpu line."""
    total = now[0] - before[0]
    return 100 * (total - (now[1] - before[1])) / total if total else 0.0


def cpu_model(cpuinfo: str) -> str:
    """'Intel(R) Core(TM) i5-7300HQ CPU @ 2.50GHz' -> 'Intel Core i5-7300HQ'."""
    name = next((line.split(":", 1)[1] for line in cpuinfo.splitlines() if line.startswith("model name")), "")
    name = name.split("@")[0]
    for noise in ("(R)", "(TM)", " CPU", " Processor"):
        name = name.replace(noise, "")
    return " ".join(name.split())


class SystemState(PollingState):
    """CPU (total and per core), memory, swap, temperatures and root disk; the bar shows a few
    keys, the system monitor panel all of them."""

    def __init__(self):
        self.viewers = 0  # open sysmon panels: frequencies, load and disk are read only for them
        self.previous_cpu: list[tuple[int, int]] | None = None
        self.sensors = self.find_sensors()
        self.disk_sensor = next(self.hwmon_inputs("nvme"), None)
        self.frequencies = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq"))
        super().__init__({"cpu": 0.0, "cores": [], "used": 0.0, "total": 0.0, "swap_used": 0.0, "swap_total": 0.0, "temp": 0.0,
                          "freq": 0.0, "load": 0.0, "disk_used": 0.0, "disk_total": 0.0, "disk_temp": 0.0})

    @staticmethod
    def cpu() -> list[tuple[int, int]]:
        """(total, idle) jiffies of the aggregate line, then of every core."""
        times = []
        for line in Path("/proc/stat").read_text().splitlines():
            if not line.startswith("cpu"):
                break
            values = [int(value) for value in line.split()[1:]]
            times.append((sum(values), values[3] + (values[4] if len(values) > 4 else 0)))
        return times

    @staticmethod
    def memory() -> dict[str, int]:
        return {
            key.rstrip(":"): int(value) * 1024
            for key, value, *_ in (line.split() for line in Path("/proc/meminfo").read_text().splitlines())
        }

    @staticmethod
    def hwmon_inputs(name: str):
        for value_file in sorted(Path("/sys/class/hwmon").glob("hwmon*/temp1_input")):
            try:
                if (value_file.parent / "name").read_text().strip() == name:
                    yield value_file
            except OSError:
                pass

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

    @staticmethod
    def read_number(path: Path) -> float | None:
        try:
            return float(path.read_text())
        except (OSError, ValueError):
            return None

    def temperature(self) -> float:
        values = [value / 1000 for value in map(self.read_number, self.sensors) if value is not None]
        return max(values, default=0.0)

    def read(self) -> dict[str, Any]:
        if mock.ENABLED:
            return mock.system()
        times = self.cpu()
        before = self.previous_cpu if self.previous_cpu and len(self.previous_cpu) == len(times) else times
        self.previous_cpu = times
        loads = [busy(now, old) for now, old in zip(times, before)]
        memory = self.memory()
        value = {
            **self.value,
            "cpu": loads[0],
            "cores": loads[1:],
            "used": memory["MemTotal"] - memory.get("MemAvailable", memory["MemFree"]),
            "total": memory["MemTotal"],
            "swap_used": memory.get("SwapTotal", 0) - memory.get("SwapFree", 0),
            "swap_total": memory.get("SwapTotal", 0),
            "temp": self.temperature(),
        }
        if self.viewers:
            frequencies = [value for value in map(self.read_number, self.frequencies) if value is not None]
            disk = os.statvfs("/")
            disk_temp = self.read_number(self.disk_sensor) if self.disk_sensor else None
            value.update(
                freq=sum(frequencies) / len(frequencies) / 1e6 if frequencies else 0.0,  # kHz -> GHz
                load=float(Path("/proc/loadavg").read_text().split()[0]),
                disk_used=(disk.f_blocks - disk.f_bfree) * disk.f_frsize,
                disk_total=disk.f_blocks * disk.f_frsize,
                disk_temp=(disk_temp or 0) / 1000,
            )
        return value


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
        if mock.ENABLED:
            return mock.network()
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


class BacklightState(PollingState):
    """Screen brightness in percent, None without a backlight (desktop monitors).
    Writes go through elogind's SetBrightness, so no video group or root is needed."""

    def __init__(self):
        self.device = Path("/mock/backlight") if mock.ENABLED else next(Path("/sys/class/backlight").glob("*"), None)
        self.max = 100 if mock.ENABLED else int((self.device / "max_brightness").read_text()) if self.device else 0
        self.hold = 0.0
        super().__init__()

    def tick(self) -> bool:
        # busctl is async: right after set() sysfs still has the old value and would yank the slider back
        if time.monotonic() >= self.hold:
            super().tick()
        return True

    def read(self) -> int | None:
        if mock.ENABLED:
            return self.value if self.value is not None else 72
        if not self.device:
            return None
        return round(int((self.device / "brightness").read_text()) * 100 / self.max)

    def set(self, percent: int) -> None:
        if mock.ENABLED:
            self.emit(max(1, min(percent, 100)))
            return
        if not self.device:
            return
        raw = max(1, round(min(percent, 100) * self.max / 100))  # never 0: a black screen is hard to undo
        run("busctl", "call", "org.freedesktop.login1", "/org/freedesktop/login1/session/auto",
            "org.freedesktop.login1.Session", "SetBrightness", "ssu", "backlight", self.device.name, str(raw))
        self.hold = time.monotonic() + 1
        self.emit(round(raw * 100 / self.max))


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
        if mock.ENABLED:
            State.__init__(self, {"layout": "us", "caps": False})
            return
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
