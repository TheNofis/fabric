"""Music popup (Super+M) over MPRIS.

    player.py  the MPRIS player over D-Bus and its controls
    logic.py   track time, the local cover file
    window.py  the popup
"""

from __future__ import annotations

from modules.base import Module
from modules.music import logic
from modules.music.player import Player
from modules.music.window import MusicWindow


class Music(Module):
    name = "music"
    action = "toggle-music"

    def build(self) -> list[MusicWindow]:
        return [MusicWindow(self.shell.monitors[0], Player())]

    @staticmethod
    def check() -> None:
        logic.check()
