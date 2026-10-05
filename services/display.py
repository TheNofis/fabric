"""Display color: contrast, gamma and warmth through XRandR gamma ramps, plus named presets.

Backlight brightness stays in BacklightState; a preset stores it next to the ramp settings.
Ramps live in the X server, so they are reapplied from the saved settings on startup.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

from services import mock
from services.state import State

FILE = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "fabric-shell" / "display.json"
NEUTRAL = {"contrast": 100, "gamma": 100, "warmth": 6500}  # contrast %, gamma x100, warmth K
LIMITS = {"brightness": (1, 100), "contrast": (50, 150), "gamma": (50, 200), "warmth": (2500, 6500)}
SEED = [
    {"name": "Day", "brightness": 72, "contrast": 100, "gamma": 100, "warmth": 6500},
    {"name": "Evening", "brightness": 55, "contrast": 100, "gamma": 100, "warmth": 4500},
    {"name": "Night", "brightness": 25, "contrast": 95, "gamma": 105, "warmth": 3400},
]


def clamp(key: str, value: Any) -> int:
    low, high = LIMITS[key]
    return max(low, min(int(value), high))


def whitepoint(kelvin: int) -> tuple[float, float, float]:
    """Relative RGB gain of a blackbody at `kelvin` (Tanner Helland's fit), 6500K = 1:1:1."""

    def rgb(k: float) -> tuple[float, float, float]:
        t = k / 100
        g = 0.39008157876901960784 * math.log(t) - 0.63184144378862745098
        b = 0.54320678911019607843 * math.log(t - 10) - 1.19625408914 if t > 19 else 0.0
        return 1.0, g, b

    return tuple(min(1.0, max(0.0, c / n)) for c, n in zip(rgb(kelvin), rgb(6500)))  # type: ignore[return-value]


def ramp(size: int, contrast: int, gamma: int, warmth: int) -> list[list[int]]:
    """Red, green and blue ramps of `size` 16-bit entries; contrast pivots on mid-gray."""
    gains = whitepoint(warmth)
    out: list[list[int]] = [[], [], []]
    for i in range(size):
        x = (i / (size - 1) - 0.5) * contrast / 100 + 0.5
        y = min(1.0, max(0.0, x)) ** (100 / gamma)
        for channel, gain in zip(out, gains):
            channel.append(round(y * gain * 65535))
    return out


class _Gamma(ctypes.Structure):
    _fields_ = [("size", ctypes.c_int)] + [(name, ctypes.POINTER(ctypes.c_ushort)) for name in ("red", "green", "blue")]


class _Resources(ctypes.Structure):
    _fields_ = [("timestamp", ctypes.c_ulong), ("config_timestamp", ctypes.c_ulong), ("ncrtc", ctypes.c_int), ("crtcs", ctypes.POINTER(ctypes.c_ulong))]


_x = None


def _xrandr() -> tuple[Any, Any, int]:
    global _x
    if _x is None:
        xlib = ctypes.CDLL(ctypes.util.find_library("X11"))
        xrandr = ctypes.CDLL(ctypes.util.find_library("Xrandr"))
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        xlib.XFlush.argtypes = [ctypes.c_void_p]
        xrandr.XRRGetScreenResourcesCurrent.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        xrandr.XRRGetScreenResourcesCurrent.restype = ctypes.POINTER(_Resources)
        xrandr.XRRFreeScreenResources.argtypes = [ctypes.POINTER(_Resources)]
        xrandr.XRRGetCrtcGammaSize.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        xrandr.XRRAllocGamma.argtypes = [ctypes.c_int]
        xrandr.XRRAllocGamma.restype = ctypes.POINTER(_Gamma)
        xrandr.XRRSetCrtcGamma.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_Gamma)]
        xrandr.XRRFreeGamma.argtypes = [ctypes.POINTER(_Gamma)]
        display = xlib.XOpenDisplay(None)
        _x = (xlib, xrandr, display)
    return _x


def set_gamma(contrast: int, gamma: int, warmth: int) -> None:
    """Load the ramp into every CRTC. ponytail: CRTCs that appear later (hotplug) keep the
    identity ramp until the next change; listen for RRScreenChangeNotify if that matters."""
    if mock.ENABLED:
        return
    xlib, xrandr, display = _xrandr()
    resources = xrandr.XRRGetScreenResourcesCurrent(display, xlib.XDefaultRootWindow(display))
    try:
        for index in range(resources.contents.ncrtc):
            crtc = resources.contents.crtcs[index]
            size = xrandr.XRRGetCrtcGammaSize(display, crtc)
            if size < 2:
                continue  # disabled CRTC
            table = xrandr.XRRAllocGamma(size)
            for name, values in zip(("red", "green", "blue"), ramp(size, contrast, gamma, warmth)):
                ctypes.memmove(getattr(table.contents, name), (ctypes.c_ushort * size)(*values), size * 2)
            xrandr.XRRSetCrtcGamma(display, crtc, table)
            xrandr.XRRFreeGamma(table)
    finally:
        xrandr.XRRFreeScreenResources(resources)
    xlib.XFlush(display)


class DisplayState(State):
    """{"settings": contrast/gamma/warmth, "active": preset name or "", "presets": [...]}."""

    def __init__(self, backlight: State, path: Path = FILE):
        self.backlight = backlight
        self.path = path
        data: dict[str, Any] = {}
        if not mock.ENABLED:
            try:
                data = json.loads(path.read_text())
            except FileNotFoundError:
                pass
            except ValueError as error:
                path.replace(path.with_suffix(".broken.json"))
                print(f"display: {path} unreadable ({error}), moved aside", file=sys.stderr)
        settings = {key: clamp(key, data.get("settings", {}).get(key, value)) for key, value in NEUTRAL.items()}
        presets = [self.clean(p) for p in data.get("presets", SEED) if isinstance(p, dict) and str(p.get("name", "")).strip()]
        super().__init__({"settings": settings, "active": str(data.get("active", "Day" if mock.ENABLED else "")), "presets": presets})
        set_gamma(**settings)

    @staticmethod
    def clean(preset: dict[str, Any]) -> dict[str, Any]:
        return {"name": str(preset["name"]).strip()[:24], **{key: clamp(key, preset.get(key, NEUTRAL.get(key, 100))) for key in LIMITS}}

    def preset(self, name: str) -> dict[str, Any] | None:
        return next((p for p in self.value["presets"] if p["name"] == name), None)

    def modified(self) -> bool:
        """True when the screen no longer matches the active preset."""
        active = self.preset(self.value["active"])
        if active is None:
            return False
        brightness = self.backlight.value
        return any(active[key] != value for key, value in self.value["settings"].items()) or (
            brightness is not None and abs(active["brightness"] - brightness) > 1)  # sysfs rounding

    def commit(self, **changes: Any) -> None:
        value = {**self.value, **changes}
        if value == self.value:
            return
        if value["settings"] != self.value["settings"]:
            set_gamma(**value["settings"])
        self.emit(value)
        self.save()

    def adjust(self, **settings: int) -> None:
        self.commit(settings={**self.value["settings"], **{key: clamp(key, v) for key, v in settings.items()}})

    def reset(self) -> None:
        self.commit(settings=dict(NEUTRAL))

    def apply(self, name: str) -> None:
        preset = self.preset(name)
        if preset is None:
            return
        self.backlight.set(preset["brightness"])
        self.commit(settings={key: preset[key] for key in NEUTRAL}, active=name)

    def store(self, name: str) -> None:
        """Save the current look as `name`; an existing preset with that name is replaced in place."""
        name = name.strip()[:24]
        if not name:
            return
        preset = self.clean({"name": name, "brightness": self.backlight.value or 100, **self.value["settings"]})
        presets = [preset if p["name"] == name else p for p in self.value["presets"]]
        if self.preset(name) is None:
            presets.append(preset)
        self.commit(presets=presets, active=name)

    def delete(self, name: str) -> None:
        self.commit(presets=[p for p in self.value["presets"] if p["name"] != name],
                    active="" if self.value["active"] == name else self.value["active"])

    def save(self) -> None:
        if mock.ENABLED:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.value, ensure_ascii=False, indent=1))
        temp.replace(self.path)
