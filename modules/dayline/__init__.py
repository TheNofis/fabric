"""Dayline (Super+C): calendar notes; a timed note becomes a notification when it is due.

    times.py   typed times, stepping, recurrence, day names: plain Python, no GTK
    store.py   Note and Notes, the notes.json store
    icloud.py  two-way iCloud Reminders sync and the `login` command
    window.py  the panel
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any


def remind(notes: Any, now: datetime) -> None:
    from modules.dayline.times import relative
    from shared.widgets import run

    for note in notes.due(now):
        # a reminder missed while the shell was off still shows, unless it is stale
        if now - note.when < timedelta(hours=12):
            run("notify-send", "-a", "Dayline", "-i", "x-office-calendar", note.text, f"{note.time} · {relative(date.fromisoformat(note.day), now.date())}")


def build(context: Any) -> list[Any]:
    # imports here, not at the top: `python -m modules.dayline.<file>` then loads that file once, not twice
    from modules.dayline.icloud import Sync
    from modules.dayline.store import Notes
    from modules.dayline.window import DaylineWindow
    from services import mock
    from shared.widgets import run

    context.notes = Notes()
    context.dayline = DaylineWindow(context.monitors[0], context.clock, context.notes)
    context.clock.subscribe(lambda now: remind(context.notes, now))
    if not mock.ENABLED:
        sync = Sync(context.notes, lambda title, body: run("notify-send", "-a", "Dayline", "-i", "x-office-calendar", title, body))
        context.dayline.connect("show", lambda *_: sync.run())  # fresh from iCloud whenever the panel opens
    return [context.dayline]
