"""On-screen display for volume and keyboard layout changes."""

from __future__ import annotations

from modules.base import Module
from modules.volume_osd.window import VolumeOSD


class VolumeOsd(Module):
    name = "volume_osd"
    action = "show-volume-osd"

    def build(self) -> list[VolumeOSD]:
        return [VolumeOSD(self.shell.monitors[0], self.shell.audio, self.shell.keyboard)]

    def activate(self) -> None:
        self.windows[0].open_temporarily()
