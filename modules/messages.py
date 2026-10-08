"""Messages (Super+T): the iPhone's texts through tether, read and answered from the desktop.

One column that pushes: the conversations, and a click (or Enter) slides the conversation in over
them; Esc slides back. The search field filters conversations and, below them, the iPhone's
contacts, so a new conversation starts by typing a name or a number. One-time codes get a copy
chip; the open conversation is marked read on the iPhone.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Callable

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.overlay import Overlay
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Gdk, GLib, Gtk, Pango

from services import mock
from services.monitors import Monitor
from services.tether import Message, Tether, Thread, code_in, initials
from shared.ui import Glider
from shared.widgets import copy_text, css, flag, text
from shared.window import PopupWindow

RUN_GAP = 5 * 60  # messages closer than this, same side, read as one run under one meta line
SHOWN = 120  # a long history opens on its latest messages; "Earlier messages" shows the rest
CODE_FRESH = 3600  # the list offers a code's copy chip only while it can still be used


def now() -> datetime:
    return mock.NOW if mock.ENABLED else datetime.now()


def when(timestamp: float, today: datetime) -> str:
    """Thread list time: 14:05, Yesterday, Mon, 5 Oct."""
    moment = datetime.fromtimestamp(timestamp)
    days = (today.date() - moment.date()).days
    if days <= 0:
        return f"{moment:%H:%M}"
    if days == 1:
        return "Yesterday"
    return f"{moment:%a}" if days < 7 else f"{moment.day} {moment:%b}" if moment.year == today.year else f"{moment.day} {moment:%b %Y}"


def day_label(moment: datetime, today: datetime) -> str:
    days = (today.date() - moment.date()).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    return f"{moment:%A}" if days < 7 else f"{moment:%A}, {moment.day} {moment:%B}" + ("" if moment.year == today.year else f" {moment.year}")


def phone(address: str) -> str:
    """+79220827679 → +7 922 082-76-79; anything else as it came."""
    digits = re.sub(r"\D", "", address)
    if len(digits) == 11 and digits[0] in "78":
        return f"{'+7' if digits[0] == '7' or address.startswith('+') else '8'} {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:]}"
    return address


def dialable(query: str) -> str | None:
    """A typed number to start a conversation with: 'tel:+79…', or None."""
    cleaned = re.sub(r"[\s()-]", "", query)
    return f"tel:{cleaned}" if re.fullmatch(r"\+?\d{5,15}", cleaned) else None


def avatar(name: str, *classes: str) -> Gtk.Label:
    """Initials on a neutral disc; a number or a sender without letters gets the person glyph."""
    letters = initials(name)
    label = text(letters or "󰀄", "msg-avatar", *classes, *(() if letters else ("glyph",)))
    label.set_valign(Gtk.Align.START)
    return label


def line(value: str, *classes: str, lines: int = 1, chars: int = 1) -> Gtk.Label:
    label = text(value, *classes, xalign=0)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.set_max_width_chars(chars)  # the column, not the text, sets the width
    label.set_hexpand(True)
    if lines > 1:
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_lines(lines)
    return label


def lift(row: Gtk.Widget, *inner: Gtk.Widget) -> None:
    """GTK3 maps a button's input window above its children: raise the inner buttons back on top."""
    row.connect_after("map", lambda *_: [button.get_event_window().raise_() for button in inner if button.get_event_window()])


class MessagesWindow(PopupWindow):
    def __init__(self, monitor: Monitor, tether: Tether):
        self.tether = tether
        self.items: list[tuple[str, Any]] = []  # the list's rows in order: ("thread", Thread) / ("new", (name, id))
        self.cursor = 0
        self.thread: tuple[str, str, bool] | None = None  # the open conversation: id, name, repliable
        self.history: list[Message] = []
        self.everything = False  # "Earlier messages" was clicked
        self.pending: dict[str, list[dict[str, Any]]] = {}  # thread → replies on their way: {"body", "failed"}
        self.drafts: dict[str, str] = {}
        self.loading = False
        self.row_widgets: list[Gtk.Widget] = []
        self.flash = 0  # the caption's "Copied" lasts a moment

        # the conversations
        self.status = text("", "msg-status", xalign=1)
        compose = Button(style_classes=("msg-icon-button",), child=text("󰏫"), tooltip_text="New message · Ctrl+N", on_clicked=lambda *_: self.compose())
        compose.set_can_focus(False)
        self.search = Entry(h_expand=True, style_classes=("msg-search-entry",))
        self.search_hint = text("Search or start a conversation", "msg-placeholder", xalign=0)
        self.search.connect("changed", lambda *_: self.on_search())
        self.search.connect("activate", lambda *_: self.open_cursor())
        self.rows = Box(orientation="v", spacing=1, style_classes=("msg-rows",))
        Glider(self.rows, "msg-thumb", follow="selected")
        self.list_scroll = ScrolledWindow(h_scrollbar_policy="never", v_scrollbar_policy="automatic", child=self.rows, v_expand=True, propagate_height=False, style_classes=("msg-scroll",))
        self.list_caption = text("", "msg-caption", "msg-inset", xalign=0)
        list_page = Box(orientation="v", spacing=12, style_classes=("msg-page",), children=[
            Box(spacing=8, style_classes=("msg-inset",), children=[text("Messages", "msg-title", xalign=0), Box(h_expand=True), self.status, compose]),
            Box(spacing=8, style_classes=("msg-field", "msg-search", "msg-inset"), children=[
                text("󰍉", "msg-field-icon"),
                Overlay(child=self.search, overlays=[self.search_hint], h_expand=True),
            ]),
            self.list_scroll,
            self.list_caption,
        ])

        # one conversation
        back = Button(style_classes=("msg-icon-button", "msg-back"), child=text("󰅁"), tooltip_text="Conversations · Esc", on_clicked=lambda *_: self.close_thread())
        back.set_can_focus(False)
        self.head_avatar = Box()
        self.head_name = line("", "msg-head-name")
        self.head_detail = line("", "msg-head-detail")
        self.head_detail.set_no_show_all(True)
        self.transcript = Box(orientation="v", style_classes=("msg-transcript",))
        self.thread_scroll = ScrolledWindow(h_scrollbar_policy="never", v_scrollbar_policy="automatic", child=self.transcript, v_expand=True, propagate_height=False, style_classes=("msg-scroll",))
        self.pinned = True  # follows the newest message until scrolled up
        adjustment = self.thread_scroll.get_vadjustment()
        adjustment.connect("changed", lambda a: self.pinned and a.set_value(a.get_upper() - a.get_page_size()))
        adjustment.connect("value-changed", lambda a: setattr(self, "pinned", a.get_value() >= a.get_upper() - a.get_page_size() - 24))
        self.reply = Entry(h_expand=True, style_classes=("msg-reply-entry",))
        self.reply_hint = text("", "msg-placeholder", xalign=0)
        self.reply.connect("changed", lambda *_: self.on_reply_typing())
        self.reply.connect("activate", lambda *_: self.send())
        self.send_button = Button(style_classes=("msg-send",), child=text("󰁝"), tooltip_text="Send · Enter", on_clicked=lambda *_: self.send())
        self.send_button.set_can_focus(False)
        self.composer = Box(spacing=8, style_classes=("msg-field", "msg-composer"), children=[
            Overlay(child=self.reply, overlays=[self.reply_hint], h_expand=True), self.send_button,
        ])
        self.readonly = text("", "msg-readonly", xalign=0)
        self.readonly.set_line_wrap(True)
        self.thread_caption = text("", "msg-caption", xalign=0)
        thread_page = Box(orientation="v", spacing=10, style_classes=("msg-page",), children=[
            Box(spacing=10, style_classes=("msg-head", "msg-inset"), children=[back, self.head_avatar, Box(orientation="v", valign="center", h_expand=True, children=[self.head_name, self.head_detail])]),
            self.thread_scroll,
            Box(orientation="v", spacing=6, style_classes=("msg-inset",), children=[self.composer, self.readonly, self.thread_caption]),
        ])
        for widget in (self.composer, self.readonly):
            widget.set_no_show_all(True)

        # the pages are see-through glass: they slide side by side across the whole panel, never over each other
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, transition_duration=220, hhomogeneous=True, vhomogeneous=True)
        self.pages.set_vexpand(True)  # fills the fixed panel, so the captions stay at the bottom
        self.pages.add_named(list_page, "list")
        self.pages.add_named(thread_page, "thread")
        panel = Box(orientation="v", style_classes=("messages",), children=[self.pages])
        panel.set_size_request(480, 680)  # fixed: pushing a conversation never resizes the window
        super().__init__(
            monitor,
            title="messages",
            dismissible=True,
            hotkey="t",
            geometry="top",
            margin=f"{monitor.height // 6}px 0px 0px 0px",
            child=panel,
        )
        self.clip_to(20, panel)
        self.connect("show", lambda *_: self.on_open())
        self.connect("map-event", lambda *_: self.take_focus())
        tether.subscribe(lambda *_: self.on_tether())
        tether.changed.append(self.reload_thread)

    # opening

    def on_open(self) -> None:
        self.tether.refresh()  # the saved list shows at once, the fresh one replaces it
        self.tether.load_contacts()
        if self.thread:
            self.reload_thread()
            self.reply.grab_focus()
        else:
            self.search.grab_focus()
        self.render_list()
        self.render_status()

    def open_named(self, name: str) -> bool:
        """From a notification's Reply: that sender's conversation, or the list searched for them."""
        match = next((thread for thread in self.tether.value["threads"] if thread.name == name or thread.address == name), None)
        if not self.get_visible():
            self.show_all()
        if match:
            self.open_thread(match.id, match.name, match.repliable)
        else:
            self.close_thread()
            self.search.set_text(name)
        return True

    def compose(self) -> None:
        self.close_thread()
        self.search.set_text("")
        self.search.grab_focus()
        self.flash_caption("Type a name or a number")

    # keyboard

    def _on_key_press(self, widget: Any, event: Gdk.EventKey) -> bool:
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        on_thread = self.pages.get_visible_child_name() == "thread"
        # keyval as on the first layout, so Ctrl+С on the Russian layout copies too
        base = Gdk.Keymap.get_for_display(self.get_display()).translate_keyboard_state(event.hardware_keycode, 0, 0)[1]
        if ctrl and base in (Gdk.KEY_c, Gdk.KEY_Insert) and on_thread and self.copy_selection():
            return True
        if ctrl and event.keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self.compose()
            return True
        if on_thread and (event.keyval == Gdk.KEY_Escape or (event.keyval == Gdk.KEY_Left and event.state & Gdk.ModifierType.MOD1_MASK)):
            self.close_thread()
            return True
        if not on_thread:
            if event.keyval == Gdk.KEY_Escape and self.search.get_text():
                self.search.set_text("")
                return True
            step = {Gdk.KEY_Up: -1, Gdk.KEY_Down: 1, Gdk.KEY_Page_Up: -6, Gdk.KEY_Page_Down: 6}.get(event.keyval)
            if step and self.items:
                self.step_cursor(step)
                return True
        return super()._on_key_press(widget, event)

    def step_cursor(self, step: int) -> None:
        self.cursor = max(0, min(len(self.items) - 1, self.cursor + step))
        self.mark_cursor()
        row = self.row_widgets[self.cursor]
        allocation, adjustment = row.get_allocation(), self.list_scroll.get_vadjustment()
        if allocation.y < adjustment.get_value():
            adjustment.set_value(allocation.y)
        elif allocation.y + allocation.height > adjustment.get_value() + adjustment.get_page_size():
            adjustment.set_value(allocation.y + allocation.height - adjustment.get_page_size())

    # the list

    def on_tether(self) -> None:
        self.render_status()
        if self.get_visible():
            self.render_list()
            if self.thread:
                self.render_composer()

    def on_search(self) -> None:
        self.search_hint.set_visible(not self.search.get_text())
        self.cursor = 0
        self.render_list()

    def matches(self) -> tuple[list[Thread], list[tuple[str, str]], str | None]:
        query = self.search.get_text().strip().casefold()
        threads = self.tether.value["threads"]
        if not query:
            return threads, [], None
        digits = re.sub(r"\D", "", query)

        def hit(*values: str) -> bool:
            return any(query in value.casefold() for value in values) or (len(digits) >= 3 and any(digits in re.sub(r"\D", "", value) for value in values))
        found = [thread for thread in threads if hit(thread.name, thread.address, thread.preview)]
        known = {thread.id for thread in threads} | {f"tel:{thread.address}" for thread in threads}
        contacts = [(name, address) for name, address in self.tether.contacts if address not in known and hit(name, address)][:6]
        number = dialable(query)
        return found, contacts, number if number and number not in known and all(number != address for _, address in contacts) else None

    def render_list(self) -> None:
        for child in self.rows.get_children():
            child.destroy()
        threads, contacts, number = self.matches()
        self.items, self.row_widgets = [], []
        today = now()
        for thread in threads:
            self.add_row(("thread", thread), self.thread_row(thread, today))
        if contacts or number:
            self.rows.add(text("New conversation", "msg-section", xalign=0))
        for name, address in contacts:
            self.add_row(("new", (name or phone(address[4:]), address)), self.contact_row(name, address))
        if number:
            self.add_row(("new", (phone(number[4:]), number)), self.contact_row("", number))
        if not self.items:
            self.rows.add(self.empty())
        self.rows.show_all()
        self.cursor = min(self.cursor, max(len(self.items) - 1, 0))
        self.mark_cursor()
        self.render_caption()

    def add_row(self, item: tuple[str, Any], row: Gtk.Widget) -> None:
        self.items.append(item)
        self.row_widgets.append(row)
        self.rows.add(row)

    def empty(self) -> Gtk.Widget:
        state = self.tether.value
        if self.search.get_text().strip():
            title, hint = "No matches", "Type a full number to start a new conversation"
        elif not state["daemon"]:
            title, hint = "tetherd isn't running", "systemctl --user start tetherd"
        elif not state["loaded"]:
            title, hint = "Loading conversations…", ""
        else:
            title, hint = "No messages yet", "Texts from the iPhone show up here"
        return Box(orientation="v", spacing=4, style_classes=("msg-empty",), valign="center", v_expand=True,
                   children=[text(title, "msg-empty-title"), *([text(hint, "msg-empty-hint")] if hint else [])])

    def thread_row(self, thread: Thread, today: datetime) -> Button:
        code = code_in(thread.preview) if today.timestamp() - thread.time < CODE_FRESH else None
        end: list[Gtk.Widget] = []
        if code:
            chip = Button(style_classes=("msg-code",), child=Box(spacing=5, children=[text("󰆏"), text(code)]),
                          tooltip_text="Copy code", on_clicked=lambda *_: self.copy(code))
            chip.set_can_focus(False)
            end.append(chip)
        elif thread.unread > 1:
            end.append(text(str(thread.unread), "msg-unread"))
        elif thread.unread:
            dot = Box(style_classes=("msg-unread-dot",))
            dot.set_valign(Gtk.Align.CENTER)
            dot.set_halign(Gtk.Align.CENTER)
            end.append(dot)
        title = line(thread.name if thread.name != thread.address else phone(thread.address), "msg-name")
        preview = line(thread.preview, "msg-preview", lines=2)
        row = Button(style_classes=("msg-row",), on_clicked=lambda *_: self.open_thread(thread.id, thread.name, thread.repliable),
                     child=Box(spacing=12, children=[
                         avatar(thread.name),
                         Box(orientation="v", spacing=2, h_expand=True, children=[
                             Box(spacing=8, children=[title, text(when(thread.time, today), "msg-time")]),
                             Box(spacing=8, children=[preview, Box(valign="center", children=end)]),
                         ]),
                     ]))
        row.set_can_focus(False)
        flag(row, "unread", thread.unread > 0)
        lift(row, *[widget for widget in end if isinstance(widget, Gtk.Button)])
        return row

    def contact_row(self, name: str, address: str) -> Button:
        shown = name or phone(address[4:])
        row = Button(style_classes=("msg-row", "contact"), on_clicked=lambda *_: self.open_thread(address, shown, True),
                     child=Box(spacing=12, children=[
                         avatar(name),
                         Box(orientation="v", valign="center", h_expand=True, children=[
                             line(shown, "msg-name"),
                             *([line(phone(address[4:]), "msg-preview")] if name else [line("New message", "msg-preview")]),
                         ]),
                     ]))
        row.set_can_focus(False)
        return row

    def mark_cursor(self) -> None:
        for index, row in enumerate(self.row_widgets):
            flag(row, "selected", index == self.cursor)
        self.rows.queue_draw()  # .selected styles nothing by itself, so GTK would not repaint until the next blink

    def open_cursor(self) -> None:
        if not self.items:
            return
        kind, payload = self.items[self.cursor]
        if kind == "thread":
            self.open_thread(payload.id, payload.name, payload.repliable)
        else:
            self.open_thread(payload[1], payload[0], True)

    def render_status(self) -> None:
        state = self.tether.value
        self.status.set_text("" if state["online"] else "iPhone offline" if state["daemon"] else "tetherd off")
        self.status.set_tooltip_text(None if state["online"] else "Saved messages only; replies wait for the iPhone link")
        flag(self.status, "offline", not state["online"])

    def render_caption(self) -> None:
        if self.flash:
            return
        hint = "↑↓ choose · Enter opens · Ctrl+N new" if self.items else ""
        self.list_caption.set_text(hint)

    def flash_caption(self, message: str) -> None:
        if self.flash:
            GLib.source_remove(self.flash)
        for caption in (self.list_caption, self.thread_caption):
            caption.set_text(message)

        def done() -> bool:
            self.flash = 0
            self.render_caption()
            self.render_thread_caption()
            return False
        self.flash = GLib.timeout_add(1800, done)

    def copy_selection(self) -> bool:
        """Ctrl+C with text selected in the transcript: the reply field keeps the keyboard focus,
        so the label never sees the shortcut. A selection in the reply field itself wins."""
        if self.reply.get_selection_bounds():
            return False
        for box in self.transcript.get_children():
            for label in box.get_children() if isinstance(box, Gtk.Box) else ():
                if isinstance(label, Gtk.Label) and label.get_selectable():
                    selected, start, end = label.get_selection_bounds()
                    if selected and start != end:
                        copy_text(label.get_text()[min(start, end):max(start, end)])
                        self.flash_caption("Copied")
                        return True
        return False

    def copy(self, code: str) -> None:
        copy_text(code)
        self.flash_caption(f"Copied {code}")

    # one conversation

    def open_thread(self, thread_id: str, name: str, repliable: bool) -> None:
        if self.thread and self.thread[0] != thread_id:
            self.drafts[self.thread[0]] = self.reply.get_text()
        self.thread, self.history, self.everything, self.pinned, self.loading = (thread_id, name, repliable), [], False, True, True
        for child in self.head_avatar.get_children():
            child.destroy()
        self.head_avatar.add(avatar(name, "small"))
        self.head_avatar.show_all()
        address = thread_id.split(":", 1)[-1]
        self.head_name.set_text(name if name != address else phone(address))
        self.head_detail.set_text(phone(address) if thread_id.startswith("tel:") and name != address else "Service messages" if not thread_id.startswith("tel:") else "")
        self.head_detail.set_visible(bool(self.head_detail.get_text()))
        self.reply.set_text(self.drafts.pop(thread_id, ""))
        self.render_transcript()
        self.render_composer()
        self.pages.set_visible_child_name("thread")
        if repliable:
            self.reply.grab_focus()
            self.reply.set_position(-1)
        self.reload_thread()

    def close_thread(self) -> None:
        if self.thread:
            self.drafts[self.thread[0]] = self.reply.get_text()
        self.thread = None
        self.pages.set_visible_child_name("list")
        self.search.grab_focus()
        self.render_list()

    def reload_thread(self) -> None:
        if not self.thread:
            return
        thread_id = self.thread[0]

        def done(messages: list[Message] | None) -> None:
            if not self.thread or self.thread[0] != thread_id:
                return
            self.loading = False
            if messages is None:  # tetherd went away: keep what is on screen
                self.render_transcript()
                return
            self.history = messages
            sent = {message.body for message in messages if message.outgoing}
            self.pending[thread_id] = [item for item in self.pending.get(thread_id, []) if item["failed"] or item["body"] not in sent]
            self.render_transcript()
            unread = [message.handle for message in messages if not message.outgoing and not message.read]
            if unread and self.get_visible():
                self.tether.mark_read(thread_id, unread)
        self.tether.messages(thread_id, done)

    def render_transcript(self) -> None:
        for child in self.transcript.get_children():
            child.destroy()
        thread_id, name = self.thread[0], self.head_name.get_text()
        today = now()
        messages = self.history if self.everything else self.history[-SHOWN:]
        if len(messages) < len(self.history):
            more = Button(style_classes=("msg-earlier",), child=text(f"Earlier messages · {len(self.history) - len(messages)}"),
                          on_clicked=lambda *_: (setattr(self, "everything", True), setattr(self, "pinned", False), self.render_transcript()))
            more.set_can_focus(False)
            more.set_halign(Gtk.Align.CENTER)
            self.transcript.add(more)
        previous: Message | None = None
        for message in messages:
            moment = datetime.fromtimestamp(message.time)
            new_day = previous is None or datetime.fromtimestamp(previous.time).date() != moment.date()
            if new_day:
                self.transcript.add(text(day_label(moment, today), "msg-day"))
            if new_day or previous.outgoing != message.outgoing or message.time - previous.time > RUN_GAP:
                self.transcript.add(self.meta("You" if message.outgoing else name, f"{moment:%H:%M}", message.outgoing))
            self.transcript.add(self.bubble(message.body, message.outgoing))
            previous = message
        for item in self.pending.get(thread_id, []):
            self.transcript.add(self.meta("You", "Not sent" if item["failed"] else "Sending…", True, failed=item["failed"], retry=item))
            self.transcript.add(self.bubble(item["body"], True, pending=True))
        if not messages and not self.pending.get(thread_id):
            self.transcript.add(Box(orientation="v", style_classes=("msg-empty",), valign="center", v_expand=True,
                                    children=[text("Loading…" if self.loading else "No messages yet", "msg-empty-title")]))
        self.transcript.show_all()

    def meta(self, who: str, stamp: str, outgoing: bool, failed: bool = False, retry: dict[str, Any] | None = None) -> Box:
        parts: list[Gtk.Widget] = [text(who, "msg-who"), text(stamp, "msg-stamp", *(("failed",) if failed else ()))]
        if failed and retry is not None:
            again = Button(style_classes=("msg-retry",), child=text("Retry"), on_clicked=lambda *_: self.resend(retry))
            again.set_can_focus(False)
            parts.append(again)
        box = Box(spacing=8, style_classes=("msg-meta",), children=parts)
        flag(box, "out", outgoing)
        return box

    def bubble(self, body: str, outgoing: bool, pending: bool = False) -> Box:
        label = text(body, "msg-text", xalign=0)
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(46)
        label.set_selectable(True)  # copy a fragment with the mouse
        label.set_can_focus(False)
        parts: list[Gtk.Widget] = [label]
        code = code_in(body)
        if code and not outgoing:
            chip = Button(style_classes=("msg-code",), child=Box(spacing=5, children=[text("󰆏"), text(code)]),
                          tooltip_text="Copy code", on_clicked=lambda *_: self.copy(code))
            chip.set_can_focus(False)
            chip.set_halign(Gtk.Align.START)
            parts.append(chip)
        box = Box(orientation="v", spacing=6, style_classes=("msg-message",), children=parts)
        flag(box, "out", outgoing)
        flag(box, "pending", pending)
        return box

    def render_composer(self) -> None:
        thread_id, name, repliable = self.thread
        online = self.tether.value["online"]
        self.composer.set_visible(repliable)
        self.readonly.set_visible(not repliable)
        self.readonly.set_text("This sender doesn't take replies" if thread_id.startswith("sender:") else "Group conversations are read-only here")
        self.reply_hint.set_text(f"Message {name.split()[0] if name and name[0].isalpha() else 'this number'}")
        self.reply.set_sensitive(online or mock.ENABLED)
        if repliable:
            self.composer.show_all()
        self.on_reply_typing()

    def on_reply_typing(self) -> None:
        self.reply_hint.set_visible(not self.reply.get_text())
        flag(self.send_button, "ready", bool(self.reply.get_text().strip()))
        self.render_thread_caption()

    def render_thread_caption(self) -> None:
        if self.flash or not self.thread:
            return
        if not self.thread[2]:
            hint = "Esc back"
        elif not self.tether.value["online"] and not mock.ENABLED:
            hint = "iPhone offline · replies are off until it reconnects"
        else:
            hint = "Enter sends · Esc back"
        self.thread_caption.set_text(hint)

    def send(self) -> None:
        body = self.reply.get_text().strip()
        if not body or not self.thread or not self.thread[2]:
            return
        self.reply.set_text("")
        item = {"body": body, "failed": False}
        self.pending.setdefault(self.thread[0], []).append(item)
        self.pinned = True
        self.deliver(self.thread[0], item)

    def resend(self, item: dict[str, Any]) -> None:
        item["failed"] = False
        self.render_transcript()
        self.deliver(self.thread[0], item)

    def deliver(self, thread_id: str, item: dict[str, Any]) -> None:
        def done(ok: bool) -> None:
            item["failed"] = not ok
            if ok and mock.ENABLED:  # no phone to echo it back: move it into the history ourselves
                self.history.append(Message("", item["body"], True, True, now().timestamp()))
                self.pending[thread_id].remove(item)
            if self.thread and self.thread[0] == thread_id:
                self.render_transcript()
                if ok and not mock.ENABLED:
                    self.reload_thread()
            self.tether.refresh()
        self.render_transcript()
        self.tether.send(thread_id, item["body"], done)


def build(context: Any) -> list[Any]:
    context.tether = Tether()
    context.messages = MessagesWindow(context.monitors[0], context.tether)
    if getattr(context, "notifications", None):
        context.notifications.reply = lambda record: context.messages.open_named(record.title)
    return [context.messages]


if __name__ == "__main__":
    today = datetime(2026, 10, 8, 21, 0)
    stamps = [today.replace(hour=9), today - timedelta(days=1), today - timedelta(days=3), today - timedelta(days=30), today - timedelta(days=400)]
    assert [when(s.timestamp(), today) for s in stamps] == ["09:00", "Yesterday", "Mon", "8 Sep", "3 Sep 2025"]
    assert [day_label(s, today) for s in stamps[:4]] == ["Today", "Yesterday", "Monday", "Tuesday, 8 September"]
    assert phone("+79220827679") == "+7 922 082-76-79" and phone("89264747777") == "8 926 474-77-77" and phone("megafon") == "megafon"
    assert dialable("+7 (922) 082-76-79") == "tel:+79220827679" and dialable("Глеб") is None and dialable("12") is None
    print("messages self-check: ok")
