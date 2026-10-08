"""OSD shown briefly on volume keys and keyboard layout switches."""

from __future__ import annotations

from typing import Any

from fabric.widgets.box import Box
from gi.repository import GLib

from services.monitors import Monitor
from services.system import KeyboardState
from services.state import JsonState
from shared.constants import CONTENT_GAP
from shared.ui import Segmented, meter
from shared.widgets import flag, text, volume_icon, volume_text
from shared.window import OverlayWindow


class VolumeOSD(OverlayWindow):
    def __init__(self, monitor: Monitor, audio: JsonState, keyboard: KeyboardState):
        # an indicator only: the window never takes focus and closes after 1.5s
        self.icon = text("󰕾", "volume-osd-icon", "volume-osd-glyph")
        self.percent = text("0%", "volume-osd-percent")
        self.scale = meter("volume-osd-meter")
        self.timer = 0
        self.volume_row = Box(spacing=8, h_expand=True, children=[self.icon, self.scale, self.percent])
        self.layouts = Segmented(tuple(name.upper() for name in KeyboardState.LAYOUTS), "layout-osd-segments")
        self.layout_row = Box(h_expand=True, style_classes=("layout-osd-row",), children=[self.layouts])
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
        self.clip_to(22, body)
        audio.subscribe(self.update)
        keyboard.subscribe(self.update_layout)

    def update(self, value: dict[str, Any]) -> None:
        muted = bool(value.get("muted"))
        volume = int(value.get("vol", 0))
        self.icon.set_label(volume_icon(volume, muted))
        self.percent.set_text(volume_text(volume, muted))
        self.scale.set_fraction(min(volume, 100) / 100)
        flag(self.scale, "dim", muted)
        flag(self.scale, "warn", volume > 100 and not muted)  # boost past 100% (i3 keys cap at 200%)

    def update_layout(self, value: dict[str, Any]) -> None:
        layout = value.get("layout")
        self.layouts.select((layout or "").upper(), animate=self.layout is not None)  # the startup state just lands
        # Caps Lock tints the picked layout cyan (the shell's caps color); the OSD opens on layout switches alone
        flag(self.layouts, "caps-lock", bool(value.get("caps")))
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
        self.timer = GLib.timeout_add(1500, self.close_timer)

    def close_timer(self) -> bool:
        self.timer = 0
        self.hide()
        return False
