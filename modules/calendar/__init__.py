"""Calendar dropdown from the bar's clock, one per monitor; a day opens it in Dayline."""

from __future__ import annotations

from modules.base import Module
from modules.calendar.window import CalendarWindow


class Calendar(Module):
    name = "calendar"

    def build(self) -> list[CalendarWindow]:
        return [CalendarWindow(monitor, self.shell.clock, self.shell) for monitor in self.shell.monitors]
