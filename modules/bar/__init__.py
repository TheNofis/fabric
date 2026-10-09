"""Status bar on every monitor; its slots open the calendar, sysmon, sound, network, Claude and display panels."""

from __future__ import annotations

from modules.base import Module
from modules.bar.window import Bar as BarWindow
from modules.bar.workspaces import Workspaces


class Bar(Module):
    name = "bar"

    def build(self) -> list[BarWindow]:
        shell, panels = self.shell, self.shell.modules
        workspaces = Workspaces()
        return [
            BarWindow(
                monitor,
                shell.clock,
                shell.system,
                workspaces,
                shell.audio,
                shell.network,
                shell.keyboard,
                panels["calendar"].windows[index],
                panels["sysmon"].windows[index],
                panels["sound"].windows[index],
                panels["network"].windows[index],
                panels["claude"].slot(index),
                shell.backlight,
                panels["display"].windows[index],
                lambda: getattr(panels.get("notifications"), "hub", None),  # built after the bar
            )
            for index, monitor in enumerate(shell.monitors)
        ]
