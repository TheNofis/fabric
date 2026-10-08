"""Calendar panel: one per monitor, dropped from the bar clock."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from gi.repository import Gdk, Gtk

from services.dayline import grid_days, month_start
from services import mock
from services.monitors import Monitor
from services.system import ClockState
from shared.ui import nav_button
from shared.widgets import css, flag, text
from shared.window import BarPanel


class CalendarWindow(BarPanel):
    def __init__(self, monitor: Monitor, clock: ClockState, context: Any):
        self.context = context  # dayline and notes are built after the calendars; read them lazily
        self.today = mock.NOW.date() if mock.ENABLED else date.today()
        self.month = month_start(self.today)

        self.day_number = text("", "cal-day", xalign=0)
        self.weekday = text("", "cal-weekday", xalign=0)
        self.full_date = text("", "cal-date", xalign=0)
        self.title = text("", "cal-title", xalign=0)
        self.back = Button(style_classes=("ui-nav", "cal-back"), child=self.title, on_clicked=lambda *_: self.show_month(self.today))

        grid = css(Gtk.Grid(column_homogeneous=True), "cal-grid")
        for column, day in enumerate(grid_days(self.month)[:7]):
            grid.attach(text(day.strftime("%a")[:2], "cal-head"), column, 0, 1, 1)
        self.cells: list[tuple[Button, Any]] = []
        for index in range(42):
            number = text("", "cal-num")
            cell = Button(style_classes=("cal-cell",), h_align="center", child=Box(orientation="v", children=[number, text("•", "cal-dot")]),
                          on_clicked=lambda *_, i=index: self.open_day(i))
            cell.set_can_focus(False)
            self.cells.append((cell, number))
            grid.attach(cell, index % 7, index // 7 + 1, 1, 1)

        super().__init__(
            monitor,
            "calendar",
            Box(
                orientation="v",
                spacing=14,
                style_classes=("popup", "cal"),
                children=[
                    Box(spacing=12, children=[self.day_number, Box(orientation="v", valign="center", children=[self.weekday, self.full_date])]),
                    Box(orientation="v", spacing=6, children=[
                        Box(children=[
                            self.back,
                            Box(h_expand=True),
                            self.nav("", -1),
                            self.nav("", 1),
                        ]),
                        grid,
                    ]),
                ],
            ),
        )
        self.add_events(Gdk.EventMask.SCROLL_MASK)
        self.connect("scroll-event", self.on_scroll)
        self.connect("show", lambda *_: self.show_month(self.today))
        clock.subscribe(self.on_clock)

    def nav(self, glyph: str, shift: int) -> Button:
        return nav_button(glyph, lambda *_: self.shift(shift))

    def shift(self, months: int) -> None:
        self.show_month(month_start(self.month, months))

    def on_scroll(self, _window: Gtk.Widget, event: Gdk.EventScroll) -> bool:
        if event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.DOWN):
            self.shift(-1 if event.direction == Gdk.ScrollDirection.UP else 1)
        return True

    def on_clock(self, now: datetime) -> None:
        if now.date() == self.today and self.day_number.get_text():
            return
        following = self.month == month_start(self.today)
        self.today = now.date()
        self.day_number.set_text(str(self.today.day))
        self.weekday.set_text(self.today.strftime("%A"))
        self.full_date.set_text(self.today.strftime("%B %Y"))
        self.show_month(self.today if following else self.month)

    def open_day(self, index: int) -> None:
        dayline = getattr(self.context, "dayline", None)
        if dayline:
            self.hide()
            dayline.show_day(grid_days(self.month)[index])

    def show_month(self, day: date) -> None:
        self.month = month_start(day)
        self.title.set_markup(f'{self.month:%B} <span fgalpha="64%">{self.month.year}</span>')
        flag(self.back, "away", self.month != month_start(self.today))
        notes = getattr(self.context, "notes", None)
        busy = {n.day for n in notes.value if n.day and not n.done and not n.deleted} if notes else set()
        for (cell, number), day in zip(self.cells, grid_days(self.month)):
            number.set_text(str(day.day))
            flag(cell, "busy", day.isoformat() in busy)
            flag(cell, "outside", day.month != self.month.month)
            flag(cell, "today", day == self.today)


def build(context: Any) -> list[Any]:
    context.calendars = [
        CalendarWindow(monitor, context.clock, context)
        for monitor in context.monitors
    ]
    return context.calendars
