"""Claude plan limits: the bar's session gauge (slot) and its dropdown.

    usage.py   the /usage poller (a thread in this process)
    logic.py   percent, time to reset, pace, warning level
    window.py  the slot and the panel
"""

from __future__ import annotations

from gi.repository import Gtk

from modules.base import Module
from modules.claude import logic
from modules.claude.usage import Usage
from modules.claude.window import ClaudeWindow, claude_slot


class Claude(Module):
    name = "claude"

    def build(self) -> list[ClaudeWindow]:
        self.usage = Usage()
        return [ClaudeWindow(monitor, self.usage, self.shell.clock) for monitor in self.shell.monitors]

    def slot(self, index: int) -> Gtk.Widget:
        """The bar slot on monitor `index`; it opens that monitor's panel."""
        return claude_slot(self.usage, self.shell.clock, self.windows[index])

    @staticmethod
    def check() -> None:
        logic.check()
