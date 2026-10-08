"""Month grid that slides when the month turns, and the big date heading beside it."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from gi.repository import Gtk

from shared.ui.buttons import nav_button
from shared.widgets import flag, text

Cell = tuple[Gtk.Button, Gtk.Label]
MONDAY = date(2024, 1, 1)


class MonthPages(Gtk.Stack):
    """Two pages of 42 day cells: turning the month fills the hidden page and slides it in
    from the side it comes from, so the direction of travel is visible."""

    def __init__(self, make_cell: Callable[[int], Cell]):
        super().__init__(transition_duration=180)
        self.pages: list[list[Cell]] = []
        for name in ("a", "b"):
            grid = Gtk.Grid(column_homogeneous=True)
            cells = [make_cell(index) for index in range(42)]
            for index, (cell, _) in enumerate(cells):
                grid.attach(cell, index % 7, index // 7, 1, 1)
            self.add_named(grid, name)
            self.pages.append(cells)
        self.current = 0

    def show(self, direction: int, fill: Callable[[list[Cell]], None]) -> None:
        """direction > 0 a later month, < 0 an earlier one, 0 refreshes the visible page in place."""
        if direction:
            self.current ^= 1
        fill(self.pages[self.current])
        kind = Gtk.StackTransitionType
        self.set_visible_child_full("ab"[self.current], kind.SLIDE_LEFT if direction > 0 else kind.SLIDE_RIGHT if direction < 0 else kind.NONE)


class MonthView(Box):
    """Month title (a click goes back to today), arrows, Monday-first weekday heads and the sliding
    grid. The module owns the month: render() paints it, on_shift(±1) asks for the next or previous
    one, on_pick(day) reports a clicked cell."""

    def __init__(self, on_back: Callable[[], Any], on_shift: Callable[[int], Any], on_pick: Callable[[date], Any]):
        self.days: list[date] = []
        self.title = text("", "ui-month-title", xalign=0)
        self.back = Button(style_classes=("ui-nav", "ui-month-back"), child=self.title, on_clicked=lambda *_: on_back())

        def make_cell(index: int) -> Cell:
            number = text("", "ui-day-num")
            cell = Button(style_classes=("ui-day-cell",), h_align="center", child=Box(orientation="v", children=[number, text("•", "ui-day-dot")]),
                          on_clicked=lambda *_: on_pick(self.days[index]))
            cell.set_can_focus(False)  # the keyboard stays where it was (Dayline's entry)
            return cell, number

        self.pages = MonthPages(make_cell)
        heads = Gtk.Grid(column_homogeneous=True)  # same columns as the pages, so the initials line up
        for column in range(7):
            heads.attach(text((MONDAY + timedelta(days=column)).strftime("%a")[:2], "ui-month-head"), column, 0, 1, 1)
        super().__init__(orientation="v", spacing=6, children=[
            Box(children=[self.back, Box(h_expand=True), nav_button("\uf053", lambda *_: on_shift(-1)), nav_button("\uf054", lambda *_: on_shift(1))]),
            Box(orientation="v", children=[heads, self.pages]),
        ])

    def render(self, month: date, days: list[date], today: date, direction: int = 0, away: bool = False,
             marks: Callable[[date], dict[str, bool]] = lambda _day: {}) -> None:
        """days: the 42 cells from the Monday before `month`; away lights the title as a way back to today;
        marks(day) flags extra cell states (busy, selected, overdue) over outside and today."""
        self.days = days
        self.title.set_markup(f'{month:%B} <span fgalpha="64%">{month.year}</span>')
        flag(self.back, "away", away)

        def fill(cells: list[Cell]) -> None:
            for (cell, number), day in zip(cells, days):
                number.set_text(str(day.day))
                flag(cell, "outside", day.month != month.month)
                flag(cell, "today", day == today)
                for name, on in marks(day).items():
                    flag(cell, name, on)

        self.pages.show(direction, fill)


class DateHeading(Box):
    """A day as a big light numeral beside its weekday and a detail line (calendar, Dayline)."""

    def __init__(self):
        self.number = text("", "ui-date-day", xalign=0)
        self.weekday = text("", "ui-date-weekday", xalign=0)
        self.detail = text("", "ui-date-detail", xalign=0)
        super().__init__(spacing=12, children=[self.number, Box(orientation="v", valign="center", children=[self.weekday, self.detail])])

    def set_day(self, day: date, detail: str) -> None:
        self.number.set_text(str(day.day))
        self.weekday.set_text(day.strftime("%A"))
        self.detail.set_text(detail)
