"""Network dropdown from the bar's network slot: Wi-Fi and wired links.

    logic.py   nmcli output to networks, profiles, links
    window.py  the panel and its nmcli calls
"""

from __future__ import annotations

from modules.base import Module
from modules.network import logic
from modules.network.window import NetworkWindow
from services.state import JsonState
from shared.constants import SCRIPTS


class Network(Module):
    name = "network"

    def build(self) -> list[NetworkWindow]:
        state = JsonState(SCRIPTS / "network.sh", {}, autostart=False)
        return [NetworkWindow(monitor, state, self.shell.network) for monitor in self.shell.monitors]

    @staticmethod
    def check() -> None:
        logic.check()
