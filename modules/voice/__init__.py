"""Voice input overlay (Super+V): scripts/voice.py types each phrase into the focused field."""

from __future__ import annotations

from modules.base import Module
from modules.voice.window import VoiceWindow


class Voice(Module):
    name = "voice"
    action = "toggle-voice"

    def build(self) -> list[VoiceWindow]:
        return [VoiceWindow(self.shell.monitors[0], self.shell.voice_state)]
