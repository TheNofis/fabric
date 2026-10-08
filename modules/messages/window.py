"""The Messages panel.

One column that pushes: the conversations, and a click (or Enter) slides the conversation in over
them; Esc slides back. The search field filters conversations and, below them, the iPhone's
contacts, so a new conversation starts by typing a name or a number. One-time codes get a copy
chip; the open conversation is marked read on the iPhone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Gdk, GLib, Gtk

from modules.messages.format import day_label, phone, search
from services import mock
from services.monitors import Monitor
from services.tether import Message, Tether, Thread, code_in, initials
from shared.ui import Glider, HintEntry, icon_button, lift_inner
from shared.widgets import copy_text, flag, line, short_time, text, wrapped
from shared.window import FocusPopup

RUN_GAP = 5 * 60  # messages closer than this, same side, read as one run under one meta line
SHOWN = 120  # a long history opens on its latest messages; "Earlier messages" shows the rest
CODE_FRESH = 3600  # the list offers a code's copy chip only while it can still be used


def empty(title: str, hint: str = "") -> Box:
    """Centred empty state of a page: what is (not) here, and what to do about it."""
    return Box(orientation="v", spacing=4, style_classes=("msg-empty",), valign="center", v_expand=True,
               children=[text(title, "msg-empty-title"), *([text(hint, "msg-empty-hint")] if hint else [])])


def avatar(name: str, *classes: str) -> Gtk.Label:
    """Initials on a neutral disc; a number or a sender without letters gets the person glyph."""
    letters = initials(name)
    label = text(letters or "󰀄", "msg-avatar", *classes, *(() if letters else ("glyph",)))
    label.set_valign(Gtk.Align.START)
    return label


class MessagesWindow(FocusPopup):
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
        compose = icon_button("󰏫", lambda *_: self.compose(), "msg-icon-button", tooltip="New message · Ctrl+N")
        search = HintEntry("Search or start a conversation", "msg-search-entry")
        self.search = search.entry
        self.search.connect("changed", lambda *_: self.on_search())
        self.search.connect("activate", lambda *_: self.open_cursor())
        self.rows = Box(orientation="v", spacing=1, style_classes=("msg-rows",))
        Glider(self.rows, "msg-thumb", follow="selected")
        self.list_scroll = ScrolledWindow(h_scrollbar_policy="never", v_scrollbar_policy="automatic", child=self.rows, v_expand=True, propagate_height=False, style_classes=("msg-scroll",))
        self.list_caption = text("", "msg-caption", "msg-inset", xalign=0)
        list_page = Box(orientation="v", spacing=12, style_classes=("msg-page",), children=[
            Box(spacing=8, style_classes=("msg-inset",), children=[text("Messages", "msg-title", xalign=0), Box(h_expand=True), self.status, compose]),
            Box(spacing=8, style_classes=("ui-input", "msg-inset"), children=[text("󰍉", "msg-field-icon"), search]),
            self.list_scroll,
            self.list_caption,
        ])

        # one conversation
        back = icon_button("󰅁", lambda *_: self.close_thread(), "msg-icon-button", "msg-back", tooltip="Conversations · Esc")
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
        reply = HintEntry("", "msg-reply-entry")
        self.reply, self.reply_hint = reply.entry, reply.hint
        self.reply.connect("changed", lambda *_: self.on_reply_typing())
        self.reply.connect("activate", lambda *_: self.send())
        self.send_button = icon_button("󰁝", lambda *_: self.send(), "msg-send", tooltip="Send · Enter")
        self.composer = Box(spacing=8, style_classes=("ui-input", "msg-composer"), children=[reply, self.send_button])
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
        super().__init__(monitor, "messages", "t", panel, drop=6)
        self.connect("show", lambda *_: self.on_open())
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
        self.cursor = 0
        self.render_list()

    def render_list(self) -> None:
        for child in self.rows.get_children():
            child.destroy()
        threads, contacts, number = search(self.search.get_text(), self.tether.value["threads"], self.tether.contacts)
        self.items, self.row_widgets = [], []
        today = mock.now()
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
        return empty(title, hint)

    def thread_row(self, thread: Thread, today: datetime) -> Button:
        code = code_in(thread.preview) if today.timestamp() - thread.time < CODE_FRESH else None
        end: list[Gtk.Widget] = []
        if code:
            end.append(self.code_chip(code))
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
                             Box(spacing=8, children=[title, text(short_time(thread.time, today), "msg-time")]),
                             Box(spacing=8, children=[preview, Box(valign="center", children=end)]),
                         ]),
                     ]))
        row.set_can_focus(False)
        flag(row, "unread", thread.unread > 0)
        lift_inner(row)
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

    def code_chip(self, code: str) -> Button:
        """A one-time code in a text: click copies it."""
        chip = Button(style_classes=("msg-code",), child=Box(spacing=5, children=[text("󰆏"), text(code)]),
                      tooltip_text="Copy code", on_clicked=lambda *_: self.copy(code))
        chip.set_can_focus(False)
        return chip

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
        today = mock.now()
        messages = self.history if self.everything else self.history[-SHOWN:]
        if len(messages) < len(self.history):
            more = icon_button(f"Earlier messages · {len(self.history) - len(messages)}", lambda *_: (setattr(self, "everything", True), setattr(self, "pinned", False), self.render_transcript()), "msg-earlier")
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
            self.transcript.add(empty("Loading…" if self.loading else "No messages yet"))
        self.transcript.show_all()

    def meta(self, who: str, stamp: str, outgoing: bool, failed: bool = False, retry: dict[str, Any] | None = None) -> Box:
        parts: list[Gtk.Widget] = [text(who, "msg-who"), text(stamp, "msg-stamp", *(("failed",) if failed else ()))]
        if failed and retry is not None:
            parts.append(icon_button("Retry", lambda *_: self.resend(retry), "msg-retry"))
        box = Box(spacing=8, style_classes=("msg-meta",), children=parts)
        flag(box, "out", outgoing)
        return box

    def bubble(self, body: str, outgoing: bool, pending: bool = False) -> Box:
        label = wrapped(body, "msg-text", chars=46)
        label.set_selectable(True)  # copy a fragment with the mouse
        label.set_can_focus(False)
        parts: list[Gtk.Widget] = [label]
        code = code_in(body)
        if code and not outgoing:
            chip = self.code_chip(code)
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
                self.history.append(Message("", item["body"], True, True, mock.now().timestamp()))
                self.pending[thread_id].remove(item)
            if self.thread and self.thread[0] == thread_id:
                self.render_transcript()
                if ok and not mock.ENABLED:
                    self.reload_thread()
            self.tether.refresh()
        self.render_transcript()
        self.tether.send(thread_id, item["body"], done)
