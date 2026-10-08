"""Password window for polkit, gnome-keyring and ssh askpass: one per agent that started."""

from __future__ import annotations

from modules.auth.window import AuthWindow
from modules.base import Module
from services import askpass, keyring, mock, polkit


class Auth(Module):
    name = "auth"

    def build(self) -> list[AuthWindow]:
        monitor, keyboard = self.shell.monitors[0], self.shell.keyboard
        if mock.ENABLED:
            return [AuthWindow(monitor, keyboard)]
        windows = []
        for service in (polkit, keyring, askpass):
            window = AuthWindow(monitor, keyboard)
            window.agent = service.start(window)
            if window.agent is not None:
                windows.append(window)
        return windows
