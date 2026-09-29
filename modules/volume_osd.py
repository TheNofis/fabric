"""OSD shown briefly on volume keys and keyboard layout switches."""

from __future__ import annotations

from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from gi.repository import GLib

from services.monitors import Monitor
from services.system import KeyboardState
from services.state import JsonState
from shared.constants import CONTENT_GAP
from shared.ui import slider
from shared.widgets import flag, text, toggle_mute, volume_icon, volume_text
from shared.window import OverlayWindow


class VolumeOSD(OverlayWindow):
    def __init__(self, monitor: Monitor, audio: JsonState, keyboard: KeyboardState):
        self.icon = Button(
            label="󰕾",
            style_classes=("volume-osd-icon", "volume-osd-glyph"),
            on_clicked=toggle_mute,
        )
        self.percent = text("0%", "volume-osd-percent")
        self.scale = slider("volume-osd-slider", max_value=101)
        self.timer = 0
        self.volume_row = Box(spacing=8, h_expand=True, children=[self.icon, self.scale, self.percent])
        self.caps = text("󰪛", "volume-osd-percent", "layout-osd-caps")
        self.layouts = {
            name: text(name.upper(), "layout-osd-item") for name in KeyboardState.LAYOUTS
        }
        self.layout_row = Box(
            spacing=8,
            h_expand=True,
            children=[
                text("󰌌", "volume-osd-icon", "volume-osd-glyph"),
                Box(spacing=4, h_expand=True, children=list(self.layouts.values())),
                self.caps,
            ],
        )
        for label in self.layouts.values():
            label.set_hexpand(True)
        self.layout = None
        body = Box(style_classes=("volume-osd",), children=[self.volume_row, self.layout_row])
        super().__init__(
            monitor,
            title="fabric-volume-osd",
            geometry="bottom",
            margin=f"0px 0px {CONTENT_GAP}px 0px",
            size=(230, 58),
            child=body,
        )
        self.clip_to(18, body)
        audio.subscribe(self.update)
        keyboard.subscribe(self.update_layout)

    def update(self, value: dict[str, Any]) -> None:
        muted = bool(value.get("muted"))
        volume = int(value.get("vol", 0))
        self.icon.set_label(volume_icon(volume, muted))
        self.percent.set_text(volume_text(volume, muted))
        self.scale.value = volume

    def update_layout(self, value: dict[str, Any]) -> None:
        layout = value.get("layout")
        for name, label in self.layouts.items():
            flag(label, "active", name == layout)
        # caps only updates the indicator; the OSD opens on layout switches alone
        flag(self.caps, "active", bool(value.get("caps")))
        # the first value is the startup state, not a switch
        if self.layout is not None and layout != self.layout:
            self.open_temporarily(self.layout_row)
        self.layout = layout

    def open_temporarily(self, row: Box | None = None) -> None:
        row = row or self.volume_row
        if self.timer:
            GLib.source_remove(self.timer)
        self.show_all()
        (self.layout_row if row is self.volume_row else self.volume_row).hide()
        self.timer = GLib.timeout_add(1000, self.close_timer)

    def close_timer(self) -> bool:
        self.timer = 0
        self.hide()
        return False


def build(context: Any) -> list[Any]:
    context.volume_osd = VolumeOSD(context.monitors[0], context.audio, context.keyboard)
    return [context.volume_osd]
