"""Dayline's panel: month grid and up next on the left, the picked day and the composer on the right.

Type and Enter adds to the picked day; click a note to edit it, then click a day to move it.
With iCloud one list shows at a time (tabs, Ctrl+Tab), the chip in the composer picks the list
a note goes to, the circle completes it.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Gdk, GLib, Gtk, Pango

from modules.dayline.store import Note, Notes
from modules.dayline.times import parse_time, relative, split_time, step_time
from services import mock
from services.monitors import Monitor
from services.system import ClockState
from shared.ui import DateHeading, Glider, HintEntry, MonthView, icon_button, lift_inner
from shared.ui.month import grid_days, month_start
from shared.widgets import css, flag, line, slide, text, wrapped
from shared.window import FocusPopup

PRIORITY_MARK = {9: "!", 5: "!!", 1: "!!!"}  # Apple: 9 low, 5 medium, 1 high
PRIORITY_CYCLE = [0, 9, 5, 1]
PRIORITY_NAME = {0: "none", 9: "low", 5: "medium", 1: "high"}
ALL = "*"  # the tab that shows every list at once


def note_text(value: str, wide: bool, *classes: str) -> Gtk.Label:
    if wide:  # up next, details: one line
        return line(value, "dayline-text", *classes)
    label = wrapped(value, "dayline-text", *classes, chars=30)  # the day column's width, so long notes wrap instead of widening it
    label.set_hexpand(True)
    return label


def list_dot(color: str) -> Gtk.Label:
    """The iCloud list's own color; lists without a usable one get the muted dot."""
    dot = text("●", "dayline-list-dot")
    if match := re.search(r"#[0-9A-Fa-f]{6}", color):  # iCloud sends {"daHexString":"#FF9500FF"}
        dot.set_markup(f'<span foreground="{match[0]}">●</span>')
    return dot


class DaylineWindow(FocusPopup):
    def __init__(self, monitor: Monitor, clock: ClockState, notes: Notes):
        self.notes = notes
        self.now = mock.now()
        self.today = self.selected = self.now.date()
        self.undated = False  # the "No date" section is the target instead of the picked day
        self.month = month_start(self.today)
        self.direction = 0  # set by turn(), consumed by render()
        self.editing: Note | None = None
        self.tab = notes.list  # the iCloud list on screen; "" without iCloud
        self.target = self.tab  # the list the composer writes to
        self.priority = 0
        self.deleted: Note | None = None  # deleted on screen, committed once the undo window passes
        self.undo_timer = 0
        self.shown_lists: list[list[str]] | None = None

        # top: one tab per iCloud list
        self.tabs = Box(spacing=2, style_classes=("dayline-tabs",))
        Glider(self.tabs, "dayline-tab-thumb", follow="active")
        self.tabs.set_no_show_all(True)

        # left: month grid with a dot under days that have notes, then what is up next
        self.grid = MonthView(lambda: self.pick(self.today), lambda shift: self.show_month(month_start(self.month, shift)), self.pick)
        self.upcoming = Box(orientation="v", spacing=2)

        # right: the picked day, its notes, the undated ones, the composer
        self.heading = DateHeading()
        self.list = Box(orientation="v", spacing=2)
        self.composer = HintEntry("New note", "dayline-entry")
        self.entry = self.composer.entry
        self.time = Entry(style_classes=("dayline-time-entry",))
        self.time.set_placeholder_text("time")
        self.time.set_width_chars(5)
        self.time.set_max_length(5)
        self.time.set_alignment(1)
        self.prio = icon_button("!", lambda *_: self.cycle_priority(), "dayline-prio")
        self.chip_dot, self.chip_title = Box(), text("", "dayline-chip-title")
        self.chip_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.chip_title.set_max_width_chars(9)
        self.chip = Button(style_classes=("dayline-chip",), child=Box(spacing=5, children=[self.chip_dot, self.chip_title]),
                           tooltip_text="List · click to change", on_clicked=lambda *_: self.cycle_target())
        self.chip.set_no_show_all(True)
        self.chip.set_can_focus(False)
        self.desc = Entry(h_expand=True, style_classes=("dayline-desc-entry",))
        self.desc.set_placeholder_text("Details")
        self.details = slide(self.desc, "down")
        self.input = Box(orientation="v", style_classes=("ui-input", "dayline-input"), children=[
            Box(spacing=8, children=[self.composer, self.prio, self.chip, self.time]),
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
            for signal in ("focus-in-event", "focus-out-event"):  # after: has_focus is updated by then
                entry.connect_after(signal, lambda *_: self.render_caption())

        scroller = ScrolledWindow(h_scrollbar_policy="never", v_scrollbar_policy="automatic", child=self.list, v_expand=True)
        scroller.set_size_request(-1, 120)  # gives way to the picker; the month column sets the height
        self.month_box = Box(orientation="v", spacing=6, style_classes=("dayline-month",), children=[
            self.grid,
            Box(orientation="v", spacing=6, style_classes=("dayline-next",), children=[text("Up next", "dayline-section", xalign=0), self.upcoming]),
        ])
        panel = Box(orientation="v", spacing=14, style_classes=("dayline",), children=[
            self.tabs,
            Box(spacing=28, children=[
                self.month_box,
                Box(orientation="v", spacing=10, h_expand=True, style_classes=("dayline-day",), children=[
                    self.heading,
                    scroller,
                    Box(orientation="v", spacing=6, children=[self.picker, self.input, self.caption]),
                ]),
            ]),
        ])
        super().__init__(monitor, "dayline", "c", panel, drop=6, width=700)
        self.add_events(Gdk.EventMask.SCROLL_MASK)
        self.connect("scroll-event", self.on_scroll)  # the notes list scrolls itself first
        self.connect("show", lambda *_: self.open())
        self.connect("hide", lambda *_: self.commit_delete())
        clock.subscribe(self.on_clock)
        notes.subscribe(lambda *_: self.render())

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

    def show_day(self, day: date) -> None:
        if not self.get_visible():
            self.show_all()  # open() picks today first
        self.pick(day)

    def on_scroll(self, _widget: Gtk.Widget, event: Gdk.EventScroll) -> bool:
        _, ox, oy = self.month_box.get_window().get_origin()
        _, x, y = event.get_root_coords()
        area = self.month_box.get_allocation()
        if not (0 <= x - ox - area.x < area.width and 0 <= y - oy - area.y < area.height):
            return False  # only the month column flips months
        if event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.DOWN):
            self.show_month(month_start(self.month, -1 if event.direction == Gdk.ScrollDirection.UP else 1))
        return True

    def show_month(self, month: date) -> None:
        self.turn(month)
        self.render()

    def pick(self, day: date) -> None:
        self.selected, self.undated = day, False
        self.turn(month_start(day))
        self.render()

    def turn(self, month: date) -> None:
        """Move to `month`; the next render slides the grid the way the month went."""
        self.direction, self.month = month.toordinal() - self.month.toordinal(), month

    def pick_undated(self) -> None:
        self.undated = not self.undated or bool(self.editing)  # while editing, a click always moves it here
        self.render()
        self.entry.grab_focus()  # the header is rebuilt by render(): keep typing going to the note
        self.entry.set_position(-1)

    def _on_key_press(self, widget: Any, event: Gdk.EventKey) -> bool:
        if event.keyval in (Gdk.KEY_z, Gdk.KEY_Z) and event.state & Gdk.ModifierType.CONTROL_MASK and self.deleted:
            self.undo_delete()
            return True
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

    def tab_ids(self) -> list[str]:
        """The tabs in order: All first once there is more than one list."""
        ids = self.list_ids()
        return [ALL, *ids] if len(ids) > 1 else ids

    @property
    def home(self) -> str:
        """The list new notes go to by default: the tab's own, or the default list under All."""
        return self.notes.list if self.tab == ALL else self.tab

    def show_tab(self, list_id: str) -> None:
        self.tab = list_id
        if not self.editing:
            self.target = self.home
        self.render()

    def switch_tab(self, shift: int) -> None:
        ids = self.tab_ids()
        if ids:
            self.show_tab(ids[(ids.index(self.tab) + shift) % len(ids) if self.tab in ids else 0])

    def cycle_target(self) -> None:
        ids = self.list_ids()
        if ids:
            self.target = ids[(ids.index(self.target) + 1) % len(ids) if self.target in ids else 0]
            self.render_composer()

    def shown(self) -> list[Note]:
        """The visible notes of the list on screen."""
        gone = self.deleted.id if self.deleted else None
        return [note for note in self.notes.value if note.id != gone and (self.tab in ("", ALL) or note.list_id == self.tab)]

    # composer

    def on_typing(self) -> None:
        flag(self.input, "invalid", False)
        self.render_composer()
        self.render_caption()
        self.render_picker()

    def cycle_priority(self) -> None:
        self.priority = PRIORITY_CYCLE[(PRIORITY_CYCLE.index(self.priority) + 1) % 4] if self.priority in PRIORITY_CYCLE else 0
        self.render_composer()

    def slot(self, label: str, on_pick: Any) -> Button:
        return icon_button(label, lambda *_: on_pick(), "dayline-slot")  # no focus: the time field keeps it, so the picker stays open

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
        self.target, self.priority = note.list_id or self.home, note.priority
        self.entry.set_text(note.text)
        self.time.set_text(note.time)
        self.desc.set_text(note.desc)
        self.entry.grab_focus()
        self.entry.set_position(-1)
        self.render()

    def cancel_edit(self) -> None:
        self.editing = None
        self.target, self.priority = self.home, 0
        for entry in (self.entry, self.time, self.desc):
            entry.set_text("")
        self.render()

    def delete(self, note: Note) -> None:
        if self.editing and self.editing.id == note.id:
            self.cancel_edit()
        self.commit_delete()
        # held back for the undo window, so iCloud never sees a delete that was undone
        self.deleted, self.undo_timer = note, GLib.timeout_add(6000, self.commit_delete)
        self.render()

    def commit_delete(self) -> bool:
        if self.undo_timer:
            GLib.source_remove(self.undo_timer)
        note, self.deleted, self.undo_timer = self.deleted, None, 0
        if note:
            self.notes.delete(note.id)  # re-renders through the subscription
        return False

    def undo_delete(self) -> None:
        GLib.source_remove(self.undo_timer)
        self.deleted, self.undo_timer = None, 0
        self.render()

    # rendering

    def render(self) -> None:
        ids = self.list_ids()
        if ids and self.tab not in self.tab_ids():
            self.tab = self.notes.list if self.notes.list in ids else ids[0]
        if ids and self.target not in ids:
            self.target = self.home if self.home in ids else ids[0]
        self.render_tabs()
        notes = self.shown()

        busy = {note.day for note in notes if note.day and not note.done}
        late = {note.day for note in notes if note.overdue(self.now)}
        self.grid.render(self.month, grid_days(self.month), self.today, self.direction, away=self.selected != self.today or self.month != month_start(self.today),
                       marks=lambda day: {"selected": day == self.selected and not self.undated, "busy": day.isoformat() in busy, "overdue": day.isoformat() in late})
        self.direction = 0
        self.heading.set_day(self.selected, f"{self.selected:%B %Y} · {relative(self.selected, self.today)}")

        for child in self.list.get_children():
            child.destroy()
        # missed notes ride along on every later day, above that day's own
        carried = [note for note in notes if note.day and note.day < self.selected.isoformat() and note.overdue(self.now)]
        for note in carried:
            self.list.add(self.row(note, f"{date.fromisoformat(note.day):%b %d}", lambda n: (self.pick(date.fromisoformat(n.day)), self.edit(n))))
        day_notes = [note for note in notes if note.day == self.selected.isoformat()]
        for note in day_notes:
            self.list.add(self.row(note, note.time or "all day", self.edit))
        day_notes += carried
        if not day_notes:
            self.list.add(text("No notes for this day", "dayline-empty", xalign=0))
        undated = [note for note in notes if not note.day]
        header = Button(style_classes=("dayline-undated",), on_clicked=lambda *_: self.pick_undated(),
                        tooltip_text="Add here, or move the note you edit here")
        header.set_can_focus(False)
        label = text("No date", xalign=0)
        label.set_hexpand(True)
        header.add(Box(children=[label, text("Move here" if self.editing else "Add here", "dayline-undated-add")]))
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
            for list_id, title, color in ([[ALL, "All", ""]] if len(self.notes.lists) > 1 else []) + self.notes.lists:
                tab = Button(style_classes=("dayline-tab",), child=Box(spacing=6, children=[*([] if list_id == ALL else [list_dot(color)]), text(title)]),
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
        self.prio.set_tooltip_text(f"Priority: {PRIORITY_NAME.get(self.priority, 'none')} · click to change")
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
        remove = icon_button("󰅖", lambda *_: self.delete(note), "dayline-delete", tooltip="Delete")
        marks = [text(PRIORITY_MARK[note.priority], "dayline-mark-priority", f"p{note.priority}")] if note.priority in PRIORITY_MARK else []
        if note.flagged:
            marks.append(text("󰈻", "dayline-mark-flag"))
        if note.repeat:
            marks.append(text("󰑖", "dayline-mark-repeat"))
        if self.tab == ALL:  # under All the note carries its list's color, so the lists stay apart
            marks.append(list_dot(next((color for list_id, _, color in self.notes.lists if list_id == note.list_id), "")))
        label = note_text(note.text, wide)
        label.set_hexpand(wide)  # the marks follow the text; up next keeps its one ellipsized line
        body = [Box(spacing=6, children=[label, Box(spacing=4, valign="start", children=marks)])]
        if note.desc and not wide:
            body.append(note_text(note.desc, True, "dayline-desc"))
        if wide:  # up next stacks the note over its date
            content = Box(orientation="v", h_expand=True, children=[*body, text(when, "dayline-when", xalign=0)])
        else:  # the day list: done circle, time column, the note
            check = icon_button("󰗠" if note.done else "󰄰", lambda *_: self.notes.toggle_done(note.id), "dayline-check",
                                tooltip="Not done" if note.done else "Next time" if note.repeat and note.day else "Done")
            content = Box(spacing=8, h_expand=True, children=[
                check,
                *([text(when, "dayline-when", "time", xalign=0)] if when else []),
                Box(orientation="v", h_expand=True, valign="center", children=body),
            ])
        row = Button(
            style_classes=("dayline-note",),
            child=Box(spacing=8, children=[content, remove]),
            on_clicked=lambda *_: on_click(note),
        )
        row.set_can_focus(False)
        lift_inner(row)
        flag(row, "past", bool(note.when and note.when <= self.now) or bool(note.day and date.fromisoformat(note.day) < self.today))
        flag(row, "done", bool(note.done))
        flag(row, "overdue", note.overdue(self.now))
        flag(row, "editing", bool(self.editing and self.editing.id == note.id))
        return row

    def render_caption(self) -> None:
        where = "No date" if self.undated else relative(self.selected, self.today) if abs((self.selected - self.today).days) < 2 else f"{self.selected:%a %d %b}"
        if self.deleted:
            gone = self.deleted.text if len(self.deleted.text) <= 24 else self.deleted.text[:23] + "…"
            hint = f"Deleted “{gone}” · Ctrl+Z to undo"
        elif self.editing and (self.editing.day or None) != (None if self.undated else self.selected.isoformat()):
            hint = f"Enter moves it to {where} · Esc cancels"
        elif self.editing:
            hint = "Editing · click a day or No date to move it · Esc cancels"
        elif self.undated:
            hint = "Enter adds to No date · click a day to date it"
        else:
            time = self.composed()[0]
            if time:
                hint = f"Enter adds to {where} at {time}"
            elif self.time.has_focus() or (self.entry.has_focus() and not self.entry.get_text()):
                hint = "↑↓ set time · or type “Call 15:30”"
            else:
                hint = f"Enter adds to {where}"
        self.caption.set_text(hint)
