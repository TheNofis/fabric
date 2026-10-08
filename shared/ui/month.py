"""Month grid that slides when the month turns."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gtk

from shared.widgets import text

Cell = tuple[Gtk.Button, Gtk.Label]


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


def weekday_heads(days: list) -> Gtk.Grid:
    """Weekday initials above MonthPages; same homogeneous columns, so they line up."""
    grid = Gtk.Grid(column_homogeneous=True)
    for column, day in enumerate(days[:7]):
        grid.attach(text(day.strftime("%a")[:2], "cal-head"), column, 0, 1, 1)
    return grid
