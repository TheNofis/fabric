"""Sound dropdown from the bar's audio slot: output and input, volume, device.

    logic.py   pactl JSON to devices
    window.py  the panel
"""

from __future__ import annotations

from modules.base import Module
from modules.sound import logic
from modules.sound.window import SoundWindow
from services.state import JsonState
from shared.constants import SCRIPTS


class Sound(Module):
    name = "sound"

    def build(self) -> list[SoundWindow]:
        state = JsonState(SCRIPTS / "sound.sh", {})
        return [SoundWindow(monitor, state) for monitor in self.shell.monitors]

    @staticmethod
    def check() -> None:
        logic.check()
