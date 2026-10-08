"""Calendar panel: one per monitor, dropped from the bar clock."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fabric.widgets.box import Box
from gi.repository import Gdk, Gtk

from services.dayline import grid_days, month_start
from services import mock
from services.monitors import Monitor
from services.system import ClockState
from shared.ui import DateHeading, MonthView
from shared.window import BarPanel


class CalendarWindow(BarPanel):
    def __init__(self, monitor: Monitor, clock: ClockState, context: Any):
        self.context = context  # dayline and notes are built after the calendars; read them lazily
        self.today = mock.now().date()
        self.month = month_start(self.today)
        self.heading = DateHeading()
        self.grid = MonthView(lambda: self.show_month(self.today, self.today.toordinal() - self.month.toordinal()), self.shift, self.open_day)
        super().__init__(monitor, "calendar", Box(orientation="v", spacing=14, style_classes=("popup", "cal"), children=[self.heading, self.grid]))
        self.add_events(Gdk.EventMask.SCROLL_MASK)
        self.connect("scroll-event", self.on_scroll)
        self.connect("show", lambda *_: self.show_month(self.today))
        clock.subscribe(self.on_clock)

    def shift(self, months: int) -> None:
        self.show_month(month_start(self.month, months), months)

    def on_scroll(self, _window: Gtk.Widget, event: Gdk.EventScroll) -> bool:
        if event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.DOWN):
            self.shift(-1 if event.direction == Gdk.ScrollDirection.UP else 1)
        return True

    def on_clock(self, now: datetime) -> None:
        if now.date() == self.today and self.heading.number.get_text():
            return
        following = self.month == month_start(self.today)
        self.today = now.date()
        self.heading.set_day(self.today, self.today.strftime("%B %Y"))
        self.show_month(self.today if following else self.month)

    def open_day(self, day: date) -> None:
        dayline = getattr(self.context, "dayline", None)
        if dayline:
            self.hide()
            dayline.show_day(day)

    def show_month(self, day: date, direction: int = 0) -> None:
        """direction: sign says which way the grid slides (0: in place)."""
        self.month = month_start(day)
        notes = getattr(self.context, "notes", None)
        busy = {n.day for n in notes.value if n.day and not n.done and not n.deleted} if notes else set()
        self.grid.render(self.month, grid_days(self.month), self.today, direction, away=self.month != month_start(self.today),
                       marks=lambda cell: {"busy": cell.isoformat() in busy})


def build(context: Any) -> list[Any]:
    context.calendars = [
        CalendarWindow(monitor, context.clock, context)
        for monitor in context.monitors
    ]
    return context.calendars
