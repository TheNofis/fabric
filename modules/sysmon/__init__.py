"""System monitor dropdown from the bar's stats: CPU graph and cores, memory, GPU."""

from __future__ import annotations

from collections import deque

from modules.base import Module
from modules.sysmon.window import HISTORY, SystemMonitorWindow
from services import mock
from services.state import JsonState
from shared.constants import SCRIPTS


class Sysmon(Module):
    name = "sysmon"

    def build(self) -> list[SystemMonitorWindow]:
        history: deque[float] = deque(maxlen=HISTORY)
        if mock.ENABLED:
            history.extend([22, 25, 24, 28, 32, 30, 26, 24, 29, 35, 42, 45, 38, 33, 28, 31, 34, 37] * 3)
        self.shell.system.subscribe(lambda value: history.append(value["cpu"]))
        gpu = JsonState(SCRIPTS / "gpu.sh", {}, autostart=False)
        return [SystemMonitorWindow(monitor, self.shell.system, gpu, history) for monitor in self.shell.monitors]
