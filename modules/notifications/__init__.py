"""Notifications: popups and the notification center (Super+N).

    record.py  NotificationRecord, reading one off the bus, the saved history
    card.py    one card, popup or history
    hub.py     NotificationHub: the popup stack, the center, timeouts, DND
"""

from __future__ import annotations

from typing import Any

from modules.base import Module
from modules.notifications.hub import NotificationHub
from services import mock


class Notifications(Module):
    name = "notifications"
    action = "toggle-notifications"

    def build(self) -> list[Any]:
        modules = self.shell.modules  # the lock is built after this
        self.hub = NotificationHub(self.shell.monitors[0], self.shell.clock, lambda: "lock" in modules and modules["lock"].locked)
        if mock.ENABLED:
            self.hub.seed_mock()
        return [self.hub.popup_window, self.hub.center_window]

    def activate(self) -> None:
        self.hub.toggle_center()
