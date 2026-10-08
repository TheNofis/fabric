"""Music popup (Super+M) over MPRIS: scripts/music.sh.

    logic.py   track time, the local cover file
    window.py  the popup
"""

from __future__ import annotations

from modules.base import Module
from modules.music import logic
from modules.music.window import MusicWindow


class Music(Module):
    name = "music"
    action = "toggle-music"

    def build(self) -> list[MusicWindow]:
        return [MusicWindow(self.shell.monitors[0], self.shell.music)]

    @staticmethod
    def check() -> None:
        logic.check()
