"""Dayline (Super+C): month grid, the notes of the picked day, and what is up next.

Notes carry a date (or none) and an optional time; a timed note becomes a notification when it is due.
Type and Enter adds to the picked day; click a note to edit it, then click a day to move it.
With an iCloud account the notes are Reminders: one list shows at a time (tabs, Ctrl+Tab),
the chip in the composer picks the list a note goes to, the circle completes it.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.overlay import Overlay
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Gdk, Gtk, Pango

from services.dayline import Note, Notes, Sync, grid_days, month_start, parse_time, split_time, step_time
from services import mock
from services.monitors import Monitor
from services.system import ClockState
from shared.ui import nav_button
from shared.widgets import css, flag, run, slide, text
from shared.window import PopupWindow

PRIORITY_MARK = {9: "!", 5: "!!", 1: "!!!"}  # Apple: 9 low, 5 medium, 1 high
PRIORITY_CYCLE = [0, 9, 5, 1]


def relative(day: date, today: date) -> str:
    delta = (day - today).days
    return {0: "Today", 1: "Tomorrow", -1: "Yesterday"}.get(delta) or (f"In {delta} days" if delta > 0 else f"{-delta} days ago")


def note_text(value: str, wide: bool, *classes: str) -> Gtk.Label:
    label = text(value, "dayline-text", *classes, xalign=0)
    label.set_hexpand(True)
    if wide:  # up next, details: one line
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(1)
    else:
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(30)  # the day column's width, so long notes wrap instead of widening it
    return label


def list_dot(color: str) -> Gtk.Label:
    """The iCloud list's own color; lists without a usable one get the muted dot."""
    dot = text("●", "dayline-list-dot")
    if match := re.search(r"#[0-9A-Fa-f]{6}", color):  # iCloud sends {"daHexString":"#FF9500FF"}
        dot.set_markup(f'<span foreground="{match[0]}">●</span>')
    return dot


class DaylineWindow(PopupWindow):
    def __init__(self, monitor: Monitor, clock: ClockState, notes: Notes):
        self.notes = notes
        self.now = mock.NOW if mock.ENABLED else datetime.now()
        self.today = self.selected = self.now.date()
        self.undated = False  # the "No date" section is the target instead of the picked day
        self.month = month_start(self.today)
        self.editing: Note | None = None
        self.tab = notes.list  # the iCloud list on screen; "" without iCloud
        self.target = self.tab  # the list the composer writes to
        self.priority = 0
        self.shown_lists: list[list[str]] | None = None

        # top: one tab per iCloud list
        self.tabs = Box(spacing=2, style_classes=("dayline-tabs",))
        self.tabs.set_no_show_all(True)

        # left: month grid with a dot under days that have notes, then what is up next
        self.title = text("", "cal-title", xalign=0)
        self.back = Button(style_classes=("ui-nav", "cal-back"), child=self.title, on_clicked=lambda *_: self.pick(self.today))
        grid = css(Gtk.Grid(column_homogeneous=True), "dayline-grid")
        for column, day in enumerate(grid_days(self.month)[:7]):
            grid.attach(text(day.strftime("%a")[:2], "cal-head"), column, 0, 1, 1)
        self.cells: list[tuple[Button, Gtk.Label]] = []
        for index in range(42):
            number = text("", "dayline-num")
            cell = Button(
                style_classes=("dayline-cell",),
                child=Box(orientation="v", children=[number, text("•", "dayline-dot")]),
                on_clicked=lambda *_, i=index: self.pick(grid_days(self.month)[i]),
            )
            cell.set_can_focus(False)  # keyboard stays in the entry; Alt+arrows move the day
            self.cells.append((cell, number))
            grid.attach(cell, index % 7, index // 7 + 1, 1, 1)
        self.upcoming = Box(orientation="v", spacing=2)

        # right: the picked day, its notes, the undated ones, the composer
        self.day_number = text("", "cal-day", xalign=0)
        self.weekday = text("", "cal-weekday", xalign=0)
        self.day_hint = text("", "cal-date", xalign=0)
        self.list = Box(orientation="v", spacing=2)
        self.entry = Entry(h_expand=True, style_classes=("dayline-entry",))
        self.placeholder = text("New note", "dayline-placeholder", xalign=0)  # GTK3 hides an entry's own while focused
        self.time = Entry(style_classes=("dayline-time-entry",))
        self.time.set_placeholder_text("time")
        self.time.set_width_chars(5)
        self.time.set_max_length(5)
        self.time.set_alignment(1)
        self.prio = Button(style_classes=("dayline-prio",), child=text("!"), tooltip_text="Priority", on_clicked=lambda *_: self.cycle_priority())
        self.chip_dot, self.chip_title = Box(), text("", "dayline-chip-title")
        self.chip_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.chip_title.set_max_width_chars(9)
        self.chip = Button(style_classes=("dayline-chip",), child=Box(spacing=5, children=[self.chip_dot, self.chip_title]),
                           tooltip_text="List · click to change", on_clicked=lambda *_: self.cycle_target())
        self.chip.set_no_show_all(True)
        for button in (self.prio, self.chip):
            button.set_can_focus(False)
        self.desc = Entry(h_expand=True, style_classes=("dayline-desc-entry",))
        self.desc.set_placeholder_text("Details")
        self.details = slide(self.desc, "down")
        self.input = Box(orientation="v", style_classes=("dayline-input",), children=[
            Box(spacing=8, children=[Overlay(child=self.entry, overlays=[self.placeholder], h_expand=True), self.prio, self.chip, self.time]),
            self.details,
        ])
        self.caption = text("", "dayline-caption", xalign=0)

        # time picker: slides open above the composer while the time field has focus
        hours = css(Gtk.Grid(column_homogeneous=True, row_spacing=2, column_spacing=2, hexpand=True), "dayline-hours")
        self.hours = [self.slot(f"{h:02}", lambda h=h: self.set_time(hour=h)) for h in range(24)]
        for h, button in enumerate(self.hours):
            hours.attach(button, h % 12, h // 12, 1, 1)
        self.minutes = [self.slot(f":{m:02}", lambda m=m: self.set_time(minute=m)) for m in (0, 15, 30, 45)]
        for index, button in enumerate([*self.minutes, self.slot("No time", lambda: self.set_time(clear=True))]):
            css(button, "minute")
            hours.attach(button, index * 2, 2, 4 if index == 4 else 2, 1)  # two hour columns each, so the rows line up
        self.picker = slide(Box(style_classes=("dayline-picker",), children=[hours]), "up")
        self.time.connect("focus-in-event", lambda *_: self.picker.reveal())
        self.time.connect("focus-out-event", lambda *_: self.picker.unreveal())
        self.time.add_events(Gdk.EventMask.SCROLL_MASK)
        self.time.connect("scroll-event", lambda _w, event: self.step(-15 if event.direction == Gdk.ScrollDirection.DOWN else 15) or True)
        for entry in (self.entry, self.time, self.desc):
            entry.connect("activate", lambda *_: self.submit())
        for entry in (self.entry, self.time):
            entry.connect("changed", lambda *_: self.on_typing())

        scroller = ScrolledWindow(h_scrollbar_policy="never", v_scrollbar_policy="automatic", child=self.list, v_expand=True)
        scroller.set_size_request(-1, 120)  # gives way to the picker; the month column sets the height
        panel = Box(orientation="v", spacing=14, style_classes=("dayline",), children=[
            self.tabs,
            Box(spacing=28, children=[
                Box(orientation="v", spacing=6, style_classes=("dayline-month",), children=[
                    Box(children=[self.back, Box(h_expand=True), self.nav("", -1), self.nav("", 1)]),
                    grid,
                    Box(orientation="v", spacing=6, style_classes=("dayline-next",), children=[text("Up next", "dayline-section", xalign=0), self.upcoming]),
                ]),
                Box(orientation="v", spacing=10, h_expand=True, style_classes=("dayline-day",), children=[
                    Box(spacing=12, children=[self.day_number, Box(orientation="v", valign="center", children=[self.weekday, self.day_hint])]),
                    scroller,
                    Box(orientation="v", spacing=6, children=[self.picker, self.input, self.caption]),
                ]),
            ]),
        ])
        super().__init__(
            monitor,
            title="dayline",
            dismissible=True,
            hotkey="c",
            geometry="top",
            margin=f"{monitor.height // 6}px 0px 0px 0px",
            size=(700, -1),
            child=panel,
        )
        self.clip_to(20, panel)
        self.add_events(Gdk.EventMask.SCROLL_MASK)
        self.connect("scroll-event", self.on_scroll)  # the notes list scrolls itself first
        self.connect("show", lambda *_: self.open())
        self.connect("map-event", lambda *_: self.take_focus())
        clock.subscribe(self.on_clock)
        notes.subscribe(lambda *_: self.render())

    def nav(self, glyph: str, shift: int) -> Button:
        return nav_button(glyph, lambda *_: self.show_month(month_start(self.month, shift)))

    def open(self) -> None:
        self.cancel_edit()
        self.pick(self.today)
        self.entry.grab_focus()

    def on_clock(self, now: datetime) -> None:
        self.now = now
        if now.date() != self.today:
            if self.selected == self.today:
                self.selected = now.date()
            self.today = now.date()
            self.month = month_start(self.selected)
        self.render()  # also re-dims notes whose time just passed

    def on_scroll(self, _widget: Gtk.Widget, event: Gdk.EventScroll) -> bool:
        if event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.DOWN):
            self.show_month(month_start(self.month, -1 if event.direction == Gdk.ScrollDirection.UP else 1))
        return True

    def show_month(self, month: date) -> None:
        self.month = month
        self.render()

    def pick(self, day: date) -> None:
        self.selected, self.month, self.undated = day, month_start(day), False
        self.render()

    def pick_undated(self) -> None:
        self.undated = not self.undated or bool(self.editing)  # while editing, a click always moves it here
        self.render()
        self.entry.grab_focus()  # the header is rebuilt by render(): keep typing going to the note
        self.entry.set_position(-1)

    def _on_key_press(self, widget: Any, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape and self.editing:
            self.cancel_edit()
            return True
        if event.keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab) and event.state & Gdk.ModifierType.CONTROL_MASK:
            self.switch_tab(-1 if event.state & Gdk.ModifierType.SHIFT_MASK else 1)
            return True
        step = {Gdk.KEY_Left: -1, Gdk.KEY_Right: 1, Gdk.KEY_Up: -7, Gdk.KEY_Down: 7}.get(event.keyval)
        if step and event.state & Gdk.ModifierType.MOD1_MASK:  # Alt+arrows walk the grid
            self.pick(self.selected + timedelta(days=step))
            return True
        if event.keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and not self.desc.has_focus():  # arrows set the time; Shift: by the hour
            self.step((15 if event.keyval == Gdk.KEY_Up else -15) * (4 if event.state & Gdk.ModifierType.SHIFT_MASK else 1))
            return True
        return super()._on_key_press(widget, event)

    # lists

    def list_ids(self) -> list[str]:
        return [item[0] for item in self.notes.lists]

    def show_tab(self, list_id: str) -> None:
        self.tab = list_id
        if not self.editing:
            self.target = list_id
        self.render()

    def switch_tab(self, shift: int) -> None:
        ids = self.list_ids()
        if ids:
            self.show_tab(ids[(ids.index(self.tab) + shift) % len(ids) if self.tab in ids else 0])

    def cycle_target(self) -> None:
        ids = self.list_ids()
        if ids:
            self.target = ids[(ids.index(self.target) + 1) % len(ids) if self.target in ids else 0]
            self.render_composer()

    def shown(self) -> list[Note]:
        """The visible notes of the list on screen."""
        return [note for note in self.notes.value if not self.tab or note.list_id == self.tab]

    # composer

    def on_typing(self) -> None:
        self.placeholder.set_visible(not self.entry.get_text())
        flag(self.input, "invalid", False)
        self.render_composer()
        self.render_caption()
        self.render_picker()

    def cycle_priority(self) -> None:
        self.priority = PRIORITY_CYCLE[(PRIORITY_CYCLE.index(self.priority) + 1) % 4] if self.priority in PRIORITY_CYCLE else 0
        self.render_composer()

    def slot(self, label: str, on_pick: Any) -> Button:
        button = Button(style_classes=("dayline-slot",), child=text(label), on_clicked=lambda *_: on_pick())
        button.set_can_focus(False)  # clicks keep focus in the time field, so the picker stays open
        return button

    def set_time(self, hour: int | None = None, minute: int | None = None, clear: bool = False) -> None:
        """An hour keeps the picked minutes (or :00); a minute keeps the hour (or the current one) and closes."""
        if clear:
            self.time.set_text("")
        else:
            current = parse_time(self.time.get_text())
            h, m = (int(current[:2]), int(current[3:])) if current else (self.now.hour, 0)
            self.time.set_text(f"{h if hour is None else hour:02}:{m if minute is None else minute:02}")
        if hour is None:
            self.entry.grab_focus()  # focus-out closes the picker
            self.entry.set_position(-1)

    def render_picker(self) -> None:
        current = parse_time(self.time.get_text())
        for h, button in enumerate(self.hours):
            flag(button, "selected", bool(current) and int(current[:2]) == h)
            flag(button, "past", self.selected == self.today and h < self.now.hour)
        for m, button in zip((0, 15, 30, 45), self.minutes):
            flag(button, "selected", bool(current) and int(current[3:]) == m)

    def step(self, minutes: int) -> None:
        self.time.set_text(step_time(self.time.get_text(), minutes, self.now))

    def composed(self) -> tuple[str | None, str]:
        """(time, text): the time field wins, else a time typed into the note ('Call 15:30'). No date: no time."""
        if self.undated:
            return "", self.entry.get_text().strip()
        time = parse_time(self.time.get_text())
        return (time, self.entry.get_text().strip()) if time != "" else split_time(self.entry.get_text())

    def submit(self) -> None:
        time, value = self.composed()
        if time is None:
            flag(self.input, "invalid", True)
            self.caption.set_text("Time looks off. Use 14:30, 9:15 or 930")
            self.time.grab_focus()
            return
        if not value:
            return
        day = None if self.undated else self.selected
        fields = {"desc": self.desc.get_text().strip(), "priority": self.priority, "list_id": self.target if self.notes.lists else ""}
        if self.editing:
            self.notes.update(self.editing.id, day, time, value, **fields)
        else:
            self.notes.add(day, time, value, **fields)
        self.cancel_edit()

    def edit(self, note: Note) -> None:
        self.editing = note
        self.undated = not note.day
        self.target, self.priority = note.list_id or self.tab, note.priority
        self.entry.set_text(note.text)
        self.time.set_text(note.time)
        self.desc.set_text(note.desc)
        self.entry.grab_focus()
        self.entry.set_position(-1)
        self.render()

    def cancel_edit(self) -> None:
        self.editing = None
        self.target, self.priority = self.tab, 0
        for entry in (self.entry, self.time, self.desc):
            entry.set_text("")
        self.render()

    def delete(self, note: Note) -> None:
        if self.editing and self.editing.id == note.id:
            self.cancel_edit()
        self.notes.delete(note.id)

    # rendering

    def render(self) -> None:
        ids = self.list_ids()
        if ids and self.tab not in ids:
            self.tab = self.notes.list if self.notes.list in ids else ids[0]
            self.target = self.target if self.target in ids else self.tab
        self.render_tabs()
        notes = self.shown()

        self.title.set_markup(f'{self.month:%B} <span fgalpha="64%">{self.month.year}</span>')
        flag(self.back, "away", self.selected != self.today or self.month != month_start(self.today))
        busy = {note.day for note in notes if note.day and not note.done}
        for (cell, number), day in zip(self.cells, grid_days(self.month)):
            number.set_text(str(day.day))
            flag(cell, "outside", day.month != self.month.month)
            flag(cell, "today", day == self.today)
            flag(cell, "selected", day == self.selected and not self.undated)
            flag(cell, "busy", day.isoformat() in busy)

        self.day_number.set_text(str(self.selected.day))
        self.weekday.set_text(self.selected.strftime("%A"))
        self.day_hint.set_text(f"{self.selected:%B %Y} · {relative(self.selected, self.today)}")

        for child in self.list.get_children():
            child.destroy()
        day_notes = [note for note in notes if note.day == self.selected.isoformat()]
        for note in day_notes:
            self.list.add(self.row(note, note.time or "all day", self.edit))
        if not day_notes:
            self.list.add(text("No notes for this day", "dayline-empty", xalign=0))
        undated = [note for note in notes if not note.day]
        header = Button(style_classes=("dayline-undated",), child=text("No date", xalign=0), on_clicked=lambda *_: self.pick_undated(),
                        tooltip_text="Add here, or move the note you edit here")
        header.set_can_focus(False)
        flag(header, "selected", self.undated)
        self.list.add(header)
        for note in undated:
            self.list.add(self.row(note, "", self.edit))
        self.list.show_all()

        for child in self.upcoming.get_children():
            child.destroy()
        coming = [note for note in notes if note.day and not note.done and (note.when or datetime.fromisoformat(note.day) + timedelta(days=1)) > self.now][:3]
        for note in coming:
            day = date.fromisoformat(note.day)
            label = relative(day, self.today) if abs((day - self.today).days) < 2 else f"{day:%a %d}"
            self.upcoming.add(self.row(note, f"{label} {note.time}".strip(), lambda n: (self.pick(date.fromisoformat(n.day)), self.edit(n)), wide=True))
        if not coming:
            self.upcoming.add(text("Nothing scheduled", "dayline-empty", xalign=0))
        self.upcoming.show_all()
        self.render_composer()
        self.render_caption()
        self.render_picker()

    def render_tabs(self) -> None:
        if self.shown_lists != self.notes.lists:
            self.shown_lists = [list(item) for item in self.notes.lists]
            for child in self.tabs.get_children():
                child.destroy()
            for list_id, title, color in self.notes.lists:
                tab = Button(style_classes=("dayline-tab",), child=Box(spacing=6, children=[list_dot(color), text(title)]),
                             on_clicked=lambda *_, i=list_id: self.show_tab(i))
                tab.set_can_focus(False)
                tab.list_id = list_id
                self.tabs.add(tab)
            self.tabs.show_all()
        self.tabs.set_visible(bool(self.notes.lists))
        for tab in self.tabs.get_children():
            flag(tab, "active", tab.list_id == self.tab)

    def render_composer(self) -> None:
        self.prio.get_child().set_text(PRIORITY_MARK.get(self.priority, "!"))
        flag(self.prio, "set", self.priority in PRIORITY_MARK)
        lists = {item[0]: item for item in self.notes.lists}
        self.chip.set_visible(bool(lists))
        if self.target in lists:
            for child in self.chip_dot.get_children():
                child.destroy()
            self.chip_dot.add(list_dot(lists[self.target][2]))
            self.chip_dot.show_all()
            self.chip_title.set_text(lists[self.target][1])
            self.chip.show_all()
        flag(self.chip, "moved", bool(self.editing and self.editing.list_id and self.target != self.editing.list_id))
        self.time.set_sensitive(not self.undated)
        if self.entry.get_text() or self.editing:
            self.details.reveal()
        else:
            self.details.unreveal()

    def row(self, note: Note, when: str, on_click: Any, wide: bool = False) -> Button:
        remove = Button(style_classes=("dayline-delete",), child=text("󰅖"), on_clicked=lambda *_: self.delete(note), tooltip_text="Delete")
        remove.set_can_focus(False)
        marks = [text(PRIORITY_MARK[note.priority], "dayline-mark-priority")] if note.priority in PRIORITY_MARK else []
        if note.flagged:
            marks.append(text("󰈻", "dayline-mark-flag"))
        if note.repeat:
            marks.append(text("󰑖", "dayline-mark-repeat"))
        body = [note_text(note.text, wide)]
        if note.desc and not wide:
            body.append(note_text(note.desc, True, "dayline-desc"))
        inner = [remove]
        if wide:  # up next stacks the note over its date
            content = Box(orientation="v", h_expand=True, children=[*body, text(when, "dayline-when", xalign=0)])
        else:  # the day list: done circle, time column, the note
            check = Button(style_classes=("dayline-check",), child=text("󰗠" if note.done else "󰄰"),
                           tooltip_text=("Not done" if note.done else "Next time" if note.repeat and note.day else "Done"),
                           on_clicked=lambda *_: self.notes.toggle_done(note.id))
            check.set_can_focus(False)
            inner.append(check)
            content = Box(spacing=8, h_expand=True, children=[
                check,
                *([text(when, "dayline-when", "time", xalign=0)] if when else []),
                Box(orientation="v", h_expand=True, valign="center", children=body),
            ])
        row = Button(
            style_classes=("dayline-note",),
            child=Box(spacing=8, children=[content, Box(spacing=4, valign="center", children=marks), remove]),
            on_clicked=lambda *_: on_click(note),
        )
        row.set_can_focus(False)
        # GTK3 maps a button's input window above its children: lift the inner buttons' back on top,
        # or the row takes their clicks
        row.connect_after("map", lambda *_: [button.get_event_window().raise_() for button in inner if button.get_event_window()])
        flag(row, "past", bool(note.when and note.when <= self.now) or bool(note.day and date.fromisoformat(note.day) < self.today))
        flag(row, "done", bool(note.done))
        flag(row, "editing", bool(self.editing and self.editing.id == note.id))
        return row

    def render_caption(self) -> None:
        where = "No date" if self.undated else relative(self.selected, self.today) if abs((self.selected - self.today).days) < 2 else f"{self.selected:%a %d %b}"
        if self.editing and (self.editing.day or None) != (None if self.undated else self.selected.isoformat()):
            hint = f"Enter moves it to {where} · Esc cancels"
        elif self.editing:
            hint = "Editing · click a day or No date to move it · Esc cancels"
        elif self.undated:
            hint = "Enter adds to No date · click a day to date it"
        else:
            time = self.composed()[0]
            hint = f"Enter adds to {where} at {time}" if time else f"Enter adds to {where} · ↑↓ or wheel set the time, or type it: Call 15:30"
        self.caption.set_text(hint)


def remind(notes: Notes, now: datetime) -> None:
    for note in notes.due(now):
        # a reminder missed while the shell was off still shows, unless it is stale
        if now - note.when < timedelta(hours=12):
            run("notify-send", "-a", "Dayline", "-i", "x-office-calendar", note.text, f"{note.time} · {relative(date.fromisoformat(note.day), now.date())}")


def build(context: Any) -> list[Any]:
    context.notes = Notes()
    context.dayline = DaylineWindow(context.monitors[0], context.clock, context.notes)
    context.clock.subscribe(lambda now: remind(context.notes, now))
    if not mock.ENABLED:
        sync = Sync(context.notes, lambda title, body: run("notify-send", "-a", "Dayline", "-i", "x-office-calendar", title, body))
        context.dayline.connect("show", lambda *_: sync.run())  # fresh from iCloud whenever the panel opens
    return [context.dayline]


if __name__ == "__main__":
    today = date(2026, 9, 28)
    assert [relative(today + timedelta(days=d), today) for d in (0, 1, -1, 3, -4)] == ["Today", "Tomorrow", "Yesterday", "In 3 days", "4 days ago"]
