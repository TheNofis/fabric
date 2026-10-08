"""Messages (Super+T): the iPhone's texts through tether (services/tether.py), read and answered from the desktop.

    format.py  phone numbers, day names, the search: plain Python, no GTK
    window.py  the panel: conversations, one conversation, the composer
"""

from __future__ import annotations

from typing import Any


def build(context: Any) -> list[Any]:
    from modules.messages.window import MessagesWindow
    from services.tether import Tether

    context.tether = Tether()
    context.messages = MessagesWindow(context.monitors[0], context.tether)
    if getattr(context, "notifications", None):
        context.notifications.reply = lambda record: context.messages.open_named(record.title)
    return [context.messages]
