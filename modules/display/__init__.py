"""Display dropdown from the bar's brightness slot: brightness, contrast, gamma, warmth, presets."""

from __future__ import annotations

from modules.base import Module
from modules.display import logic
from modules.display.window import DisplayWindow
from services.display import DisplayState


class Display(Module):
    name = "display"

    def build(self) -> list[DisplayWindow]:
        state = DisplayState(self.shell.backlight)
        return [DisplayWindow(monitor, state, self.shell.backlight) for monitor in self.shell.monitors]

    @staticmethod
    def check() -> None:
        logic.check()
