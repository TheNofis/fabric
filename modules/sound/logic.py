"""Sound without GTK: pactl JSON to the devices the panel lists."""

from __future__ import annotations

import re
from typing import Any



# "G435 Wireless Gaming Headset Digital Stereo (IEC958)" -> "G435 Wireless Gaming Headset"
PROFILE = re.compile(r"\s+(Analog |Digital )?(Stereo|Mono|Surround [\d.]+)( \(.*\))?$")


def devices(items: list[dict[str, Any]], default: str) -> list[dict[str, Any]]:
    """pactl JSON sinks/sources -> what the panel shows; monitor sources are not inputs."""
    return [
        {
            "name": item["name"],
            "label": PROFILE.sub("", item["description"]),
            "kind": item.get("properties", {}).get("device.form_factor", ""),
            "volume": max((int(channel["value_percent"].rstrip("%")) for channel in item["volume"].values()), default=0),
            "muted": item["mute"],
            "default": item["name"] == default,
        }
        for item in items
        if item.get("properties", {}).get("device.class") != "monitor"
    ]


def device_icon(kind: str, output: bool) -> str:
    if kind in ("headset", "headphone"):
        return "󰋋"
    return "󰓃" if output else "󰍬"


def check() -> None:
    sinks = [
        {"name": "hs", "description": "G435 Wireless Gaming Headset Digital Stereo (IEC958)", "mute": False,
         "volume": {"front-left": {"value_percent": "83%"}, "front-right": {"value_percent": "80%"}}, "properties": {"device.form_factor": "headset"}},
        {"name": "hs.monitor", "description": "Monitor of G435", "mute": False, "volume": {}, "properties": {"device.class": "monitor"}},
        {"name": "pci", "description": "Built-in Audio Analog Stereo", "mute": True, "volume": {"mono": {"value_percent": "5%"}}},
    ]
    assert devices(sinks, "pci") == [
        {"name": "hs", "label": "G435 Wireless Gaming Headset", "kind": "headset", "volume": 83, "muted": False, "default": False},
        {"name": "pci", "label": "Built-in Audio", "kind": "", "volume": 5, "muted": True, "default": True},
    ]
