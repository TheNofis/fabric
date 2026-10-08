"""Messages (Super+T): the iPhone's texts through tether (services/tether.py), read and answered from the desktop.

    format.py  phone numbers, day names, the search: plain Python, no GTK
    window.py  the panel: conversations, one conversation, the composer
"""

from __future__ import annotations

from modules.base import Module
from modules.messages import format
from modules.messages.window import MessagesWindow
from services.tether import Tether


class Messages(Module):
    name = "messages"
    action = "toggle-messages"

    def build(self) -> list[MessagesWindow]:
        self.tether = Tether()
        self.window = MessagesWindow(self.shell.monitors[0], self.tether)
        if notifications := self.shell.modules.get("notifications"):  # its Reply opens the conversation here
            notifications.hub.reply = lambda record: self.window.open_named(record.title)
        return [self.window]

    @staticmethod
    def check() -> None:
        format.check()
