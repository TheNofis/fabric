"""Stable demo data for screenshots and UI review (FABRIC_MOCK=1)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

ENABLED = os.environ.get("FABRIC_MOCK", "").lower() in {"1", "true", "yes"}
NOW = datetime(2026, 10, 1, 10, 30)


def now() -> datetime:
    """The clock the UI shows: frozen at NOW in mock mode."""
    return NOW if ENABLED else datetime.now()


def json_for(script: str) -> object:
    name = script.rsplit("/", 1)[-1]
    if name == "audio.sh":
        return {"vol": 68, "muted": False}
    if name == "workspaces.sh":
        return [
            {"name": name, "focused": name == "4", "visible": name in {"1", "4"}, "urgent": False, "output": "eDP-1"}
            for name in ("1", "2", "3", "4", "5", "6", "7")
        ]
    if name == "music.sh":
        return {"status": "Playing", "title": "Midnight City", "artist": "M83", "position": 112, "length": 268, "elapsed": "1:52", "duration": "4:28", "art": "", "source": "iPhone Spotify", "phone": True, "players": 2}
    if name == "gpu.sh":
        return {"name": "NVIDIA GeForce GTX 1660", "load": 42, "temp": 58, "power": 74, "mem_used": 3072, "mem_total": 6144}
    if name == "sound.sh":
        return {
            "sink": "headset", "source": "mic", "sinks": [
                {"name": "headset", "description": "Studio Headset Digital Stereo", "mute": False, "volume": {"front": {"value_percent": "68%"}}, "properties": {"device.form_factor": "headset"}},
                {"name": "speakers", "description": "Desk Speakers Analog Stereo", "mute": False, "volume": {"front": {"value_percent": "42%"}}, "properties": {"device.form_factor": "speaker"}},
            ], "sources": [
                {"name": "mic", "description": "USB Microphone Mono", "mute": False, "volume": {"front": {"value_percent": "74%"}}, "properties": {"device.form_factor": "microphone"}},
            ],
        }
    if name == "network.sh":
        return {"radio": "enabled", "wifi": "*:87:WPA2:Studio\n :64:WPA2:Home\n :42:WPA2:Guest", "known": "studio:Studio\nhome:Home", "devices": "wlan0:wifi:connected:Studio\nenp3s0:ethernet:unavailable:\n"}
    if name == "claude.py":
        return {"plan": "pro", "at": int(NOW.timestamp()), "error": "", "session": {"pct": 63, "resets": int((NOW + timedelta(hours=2, minutes=18)).timestamp()), "severity": "normal"}, "week": {"pct": 38, "resets": int((NOW + timedelta(days=4, hours=5)).timestamp()), "severity": "normal"}, "sources": [["Coding", 54], ["Review", 28], ["Planning", 18]]}
    if name == "voice.py":
        return {"state": "speaking", "partial": "Демонстрационный голосовой ввод", "last": "Демонстрационный голосовой ввод", "device": "mock"}
    return {}


def system() -> dict[str, object]:
    return {"cpu": 37.0, "cores": [31.0, 44.0, 28.0, 46.0, 35.0, 39.0, 33.0, 41.0], "used": 10.2 * 1073741824, "total": 16 * 1073741824, "swap_used": 0.4 * 1073741824, "swap_total": 4 * 1073741824, "temp": 57.0, "freq": 3.8, "load": 1.42, "disk_used": 228 * 1073741824, "disk_total": 512 * 1073741824, "disk_temp": 42.0}


def network() -> dict[str, str]:
    return {"iface": "wlan0", "ip": "192.168.1.42", "down": "1.2M", "up": "240K"}
