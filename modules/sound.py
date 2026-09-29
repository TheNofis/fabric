"""Sound panel: output and input volume and device, dropped from the bar's audio device slot."""

from __future__ import annotations

import re
from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.scale import Scale
from gi.repository import GLib

from services.monitors import Monitor
from services.state import JsonState
from shared.constants import SCRIPTS
from shared.ui import amount, big_value, check, header, list_row, panel, row_list, slider
from shared.widgets import flag, run
from shared.window import BarPanel

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


class Channel(Box):
    """One direction: big volume, mute, slider and the device list. `target` is pactl's
    sink or source; set-default moves the playing streams along (pipewire-pulse)."""

    def __init__(self, title: str, target: str):
        self.target = target
        self.output = target == "sink"
        self.shown: list[tuple[str, str, str, bool]] | None = None
        self.pending = 0
        self.syncing = False

        self.big = big_value()
        self.mute = Button(style_classes=("sound-mute",), on_clicked=lambda *_: run("pactl", f"set-{target}-mute", f"@DEFAULT_{target.upper()}@", "toggle"))
        self.scale = slider("sound-slider")
        self.scale.connect("value-changed", self.on_drag)
        self.list = row_list()
        super().__init__(
            orientation="v",
            spacing=6,
            children=[
                header(title),
                Box(children=[self.big, Box(h_expand=True), self.mute]),
                self.scale,
                self.list,
            ],
        )

    # Dragging emits dozens of value-changed per second: apply at most every 50ms, with the latest position.
    def on_drag(self, _scale: Scale) -> None:
        if not self.syncing and not self.pending:
            self.pending = GLib.timeout_add(50, self.apply)

    def apply(self) -> bool:
        self.pending = 0
        run("pactl", f"set-{self.target}-volume", f"@DEFAULT_{self.target.upper()}@", f"{int(self.scale.value)}%")
        return False

    def update(self, items: list[dict[str, Any]]) -> None:
        current = next((item for item in items if item["default"]), None)
        self.set_visible(current is not None)
        if current is None:
            return
        volume, muted = current["volume"], current["muted"]
        self.big.set_markup("Muted" if muted else amount(volume, "%"))
        flag(self.big, "dim", muted)
        icon = ("󰖁" if muted else "󰕾") if self.output else ("󰍭" if muted else "󰍬")
        self.mute.set_label(icon)
        self.mute.set_tooltip_text("Unmute" if muted else "Mute")
        flag(self.mute, "active", muted)
        flag(self.scale, "dim", muted)
        if not self.pending:  # the user is dragging; don't yank the slider back to a stale value
            self.syncing = True
            self.scale.value = min(volume, 100)
            self.syncing = False

        # rebuild the rows only when the list itself changes, not on every volume step
        shown = [(item["name"], item["label"], item["kind"], item["default"]) for item in items]
        if shown == self.shown:
            return
        self.shown = shown
        self.list.children = [self.row(*entry) for entry in shown]
        self.list.show_all()

    def row(self, name: str, label: str, kind: str, default: bool) -> Button:
        return list_row(
            device_icon(kind, self.output),
            label,
            check(default),
            classes=("default",) if default else (),
            on_clicked=lambda *_: run("pactl", f"set-default-{self.target}", name),
            tooltip=label,
            max_chars=28,
        )


class SoundWindow(BarPanel):
    def __init__(self, monitor: Monitor, state: JsonState):
        self.output = Channel("Output", "sink")
        self.input = Channel("Input", "source")
        self.output.set_no_show_all(True)
        self.input.set_no_show_all(True)
        settings = Button(
            label="Sound Settings…",
            style_classes=("sound-settings",),
            on_clicked=lambda *_: (self.hide(), run("pavucontrol")),
        )
        super().__init__(
            monitor,
            "sound",
            panel(self.output, self.input, settings),
        )
        state.subscribe(self.update)

    def update(self, value: dict[str, Any]) -> None:
        self.output.update(devices(value.get("sinks", []), value.get("sink", "")))
        self.input.update(devices(value.get("sources", []), value.get("source", "")))
        for channel in (self.output, self.input):
            if channel.get_visible():
                channel.show_all()


def build(context: Any) -> list[Any]:
    state = JsonState(SCRIPTS / "sound.sh", {})
    context.sounds = [SoundWindow(monitor, state) for monitor in context.monitors]
    return context.sounds
