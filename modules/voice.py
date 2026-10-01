"""Voice input overlay (Super+V): scripts/voice.py types each phrase into the focused field."""

from __future__ import annotations

from typing import Any

from fabric.widgets.box import Box
from gi.repository import Pango

from services.monitors import Monitor
from services.state import JsonState
from shared.constants import CONTENT_GAP
from shared.widgets import flag, text
from shared.window import OverlayWindow


class VoiceWindow(OverlayWindow):
    """Doesn't take focus (OverlayWindow), so typed text lands in the field that had it."""

    def __init__(self, monitor: Monitor, voice: JsonState):
        self.voice = voice
        self.icon = text("󰍬", "voice-osd-icon")
        self.label = text("Loading model…", "voice-osd-text", xalign=0)
        self.label.set_ellipsize(Pango.EllipsizeMode.START)  # the newest words stay visible
        self.label.set_hexpand(True)
        body = Box(spacing=10, style_classes=("volume-osd", "voice-osd"), children=[self.icon, self.label])
        super().__init__(
            monitor,
            title="fabric-voice",
            geometry="bottom",
            margin=f"0px 0px {CONTENT_GAP}px 0px",
            size=(460, 58),
            child=body,
        )
        self.clip_to(18, body)
        voice.subscribe(self.update)

    def update(self, value: dict[str, Any]) -> None:
        state = value.get("state")
        flag(self.icon, "speaking", state == "speaking")
        if state == "loading":
            self.label.set_text("Loading model…")
        elif state == "speaking":
            self.label.set_text(value.get("partial") or "…")
        else:
            self.label.set_text(value.get("last") or "Speak — phrases are typed after a pause")
        flag(self.label, "muted", state != "speaking")

    def toggle(self) -> None:
        if self.get_visible():
            self.hide()
            self.voice.stop()
            self.voice.emit({"state": "loading"})  # next open starts from a fresh process
        else:
            self.show_all()
            self.voice.start()


def build(context: Any) -> list[Any]:
    context.voice = VoiceWindow(context.monitors[0], context.voice_state)
    return [context.voice]
