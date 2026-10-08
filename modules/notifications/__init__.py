"""Notifications: popups and the notification center (Super+N).

    record.py  NotificationRecord, reading one off the bus, the saved history
    card.py    one card, popup or history
    hub.py     NotificationHub: the popup stack, the center, timeouts, DND
"""

from __future__ import annotations

from typing import Any


def build(context: Any) -> list[Any]:
    from modules.notifications.hub import NotificationHub
    from services import mock

    context.notifications = NotificationHub(context.monitors[0], context.clock, lambda: bool(getattr(context, "lock", None) and context.lock.locked))
    if mock.ENABLED:
        context.notifications.seed_mock()
    return [context.notifications.popup_window, context.notifications.center_window]
