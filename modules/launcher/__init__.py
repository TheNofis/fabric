"""Launcher (rofi replacement): apps, calc, sites, ssh, clipboard, emoji. Sources: services/launcher.py."""

from __future__ import annotations

from modules.base import Module
from modules.launcher.window import LauncherWindow


class Launcher(Module):
    name = "launcher"
    action = "toggle-launcher"

    def build(self) -> list[LauncherWindow]:
        return [LauncherWindow(self.shell.monitors[0])]
