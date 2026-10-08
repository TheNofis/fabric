"""Dayline (Super+C): calendar notes; a timed note becomes a notification when it is due.

    times.py   typed times, stepping, recurrence, day names: plain Python, no GTK
    store.py   Note and Notes, the notes.json store
    icloud.py  two-way iCloud Reminders sync and the `login` command
    window.py  the panel
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from modules.base import Module
from modules.dayline import store, times
from modules.dayline.store import Notes
from modules.dayline.window import DaylineWindow
from services import mock
from shared.widgets import run


def notify(title: str, body: str) -> None:
    run("notify-send", "-a", "Dayline", "-i", "x-office-calendar", title, body)


def remind(notes: Notes, now: datetime) -> None:
    for note in notes.due(now):
        # a reminder missed while the shell was off still shows, unless it is stale
        if now - note.when < timedelta(hours=12):
            notify(note.text, f"{note.time} · {times.relative(date.fromisoformat(note.day), now.date())}")


class Dayline(Module):
    name = "dayline"
    action = "toggle-dayline"

    def build(self) -> list[DaylineWindow]:
        self.notes = Notes()
        self.window = DaylineWindow(self.shell.monitors[0], self.shell.clock, self.notes)
        self.shell.clock.subscribe(lambda now: remind(self.notes, now))
        if not mock.ENABLED:
            # imported here: `python -m modules.dayline.icloud login` then loads icloud.py once
            from modules.dayline.icloud import Sync

            sync = Sync(self.notes, notify)
            self.window.connect("show", lambda *_: sync.run())  # fresh from iCloud whenever the panel opens
        return [self.window]

    @staticmethod
    def check() -> None:
        from modules.dayline import icloud

        times.check()
        store.check()
        icloud.check()
