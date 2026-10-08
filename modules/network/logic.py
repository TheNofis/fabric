"""Network without GTK: nmcli output to networks, saved profiles and links."""

from __future__ import annotations

import re
from typing import Any

MAX_NETWORKS = 8  # ponytail: strongest N only; a scrolled list if a crowded place needs more
FIELD = re.compile(r"(?<!\\):")


def table(raw: str) -> list[list[str]]:
    """`nmcli -t` output -> rows of fields; terse mode escapes ':' and '\\' inside values."""
    return [[field.replace("\\:", ":").replace("\\\\", "\\") for field in FIELD.split(line)] for line in raw.splitlines() if line]


def networks(raw: str, known: set[str]) -> list[dict[str, Any]]:
    """One entry per SSID (the strongest access point), connected first, then saved, then by signal."""
    seen: dict[str, dict[str, Any]] = {}
    for in_use, signal, security, ssid in (row for row in table(raw) if len(row) == 4):
        if not ssid:
            continue  # hidden network
        entry = seen.setdefault(ssid, {"ssid": ssid, "signal": 0, "secure": False, "active": False, "known": ssid in known})
        entry["signal"] = max(entry["signal"], int(signal or 0))
        entry["secure"] |= security not in ("", "--")
        entry["active"] |= in_use == "*"
    return sorted(seen.values(), key=lambda n: (not n["active"], not n["known"], -n["signal"]))[:MAX_NETWORKS]


def saved(raw: str) -> dict[str, list[str]]:
    """`uuid:ssid` lines -> SSID -> profile UUIDs (a network can have several profiles)."""
    profiles: dict[str, list[str]] = {}
    for line in raw.splitlines():
        uuid, _, ssid = line.partition(":")
        if ssid:
            profiles.setdefault(ssid, []).append(uuid)
    return profiles


def links(raw: str, kind: str) -> list[dict[str, str]]:
    return [{"device": row[0], "state": row[2], "connection": row[3]} for row in table(raw) if len(row) == 4 and row[1] == kind]


def signal_icon(signal: int) -> str:
    return "󰤟" if signal < 30 else "󰤢" if signal < 55 else "󰤥" if signal < 80 else "󰤨"


def check() -> None:
    wifi = "*:79:WPA1 WPA2:Home\n :34:WPA2:Home\n :90:--:Cafe\\: Free\n :50::\n :20:WPA2:Work\n :60:WPA2:Home 5G\n"
    assert [(n["ssid"], n["signal"], n["secure"], n["active"], n["known"]) for n in networks(wifi, {"Home", "Work"})] == [
        ("Home", 79, True, True, True), ("Work", 20, True, False, True), ("Cafe: Free", 90, False, False, False), ("Home 5G", 60, True, False, False)]
    assert saved("a1:Home\nb2:Cafe: Free\nc3:Home\nd4:\n") == {"Home": ["a1", "c3"], "Cafe: Free": ["b2"]}
    devices = "wlan0:wifi:connected:Home\n9C\\:92:bt:disconnected:\neth0:ethernet:unavailable:\n"
    assert links(devices, "ethernet") == [{"device": "eth0", "state": "unavailable", "connection": ""}]
    assert [signal_icon(s) for s in (10, 40, 70, 95)] == ["󰤟", "󰤢", "󰤥", "󰤨"]
