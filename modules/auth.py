"""Password dialogs: polkit (admin rights for pkexec, systemctl, GParted...) and
gnome-keyring (unlocking or creating a keyring, e.g. when a browser starts).

One AuthWindow per agent. The agent calls open/prompt/error/close and gets
respond(password, chosen) or cancel() back.
"""

from __future__ import annotations

from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.entry import Entry
from gi.repository import Gtk

from services import keyring, polkit
from services import mock
from services.monitors import Monitor
from shared.ui import button, password_entry, password_field
from shared.widgets import flag, text
from shared.window import PopupWindow


def _password_entry(on_activate: Any) -> tuple[Entry, Box]:
    entry = password_entry(on_activate)
    box = password_field(entry)
    box.set_no_show_all(True)
    return entry, box


class AuthWindow(PopupWindow):
    outside_click_closes = False  # a stray click must not cancel a sudo prompt

    def __init__(self, monitor: Monitor, keyboard: Any):
        self.agent: Any = None
        self.failure, self.busy, self.caps = "", False, False
        self.title = text("", "auth-title")
        self.message = text("", "auth-message")
        self.message.set_line_wrap(True)
        self.message.set_justify(Gtk.Justification.CENTER)
        self.message.set_max_width_chars(40)
        self.label = text("", "auth-label")
        for label in (self.message, self.label):
            label.set_no_show_all(True)
        self.entry, self.input = _password_entry(self._submit)
        self.repeat, self.repeat_input = _password_entry(self._submit)  # new keyring: type it twice
        self.choice = Gtk.CheckButton()
        self.choice.get_style_context().add_class("auth-check")
        self.choice.set_no_show_all(True)
        self.hint = text("", "auth-hint")
        self.hint.set_no_show_all(True)
        self.cancel_button = button("Cancel", lambda *_: self.hide())
        self.ok_button = button("Authenticate", lambda *_: self._submit(), primary=True)
        panel = Box(
            orientation="v",
            spacing=10,
            style_classes=("auth",),
            children=[
                text("\U000F0483", "auth-icon"),  # nf-md-shield_lock
                self.title,
                self.message,
                self.label,
                self.input,
                self.repeat_input,
                self.choice,
                self.hint,
                Box(spacing=8, h_align="end", children=[self.cancel_button, self.ok_button]),
            ],
        )
        # dismissible: keyboard grab while open; Esc cancels
        super().__init__(monitor, title="fabric-auth", dismissible=True, geometry="center", size=(400, -1), child=panel)
        keyboard.subscribe(lambda v: (setattr(self, "caps", bool(v.get("caps"))), self._update_hint()))
        self.clip_to(20, panel)
        self.connect("map-event", lambda *_: (self.take_focus(), self._focus()))
        self.connect("hide", lambda *_: self.agent is not None and self.agent.cancel())

    # agent callbacks
    def open(self, title: str, message: str) -> None:
        self.title.set_text(title)
        self.message.set_text(message)
        self.message.set_visible(bool(message))
        self.failure = ""
        self._update_hint()
        flag(self.input, "failed", False)
        self.show_all()

    def prompt(self, label: str, echo: bool = False, password: bool = True, repeat: bool = False,
               choice: str = "", chosen: bool = False, ok: str = "Authenticate", cancel: str = "Cancel") -> None:
        self.label.set_text(label)
        self.label.set_visible(bool(label))
        self.input.set_visible(password)
        self.repeat_input.set_visible(password and repeat)
        self.entry.set_visibility(echo)
        for entry in (self.entry, self.repeat):
            entry.set_text("")
        self.choice.set_label(choice)
        self.choice.set_active(chosen)
        self.choice.set_visible(bool(choice))
        self.ok_button.set_label(ok)
        self.cancel_button.set_label(cancel)
        self._set_busy(False)
        self._focus()

    def error(self, value: str) -> None:
        self.failure = value.strip()
        self._update_hint()
        flag(self.input, "failed", True)

    def close(self) -> None:
        self.hide()  # the agent already finished the request, so the hide handler's cancel is a no-op

    def _focus(self) -> None:
        (self.entry if self.input.get_visible() else self.ok_button).grab_focus()

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        for widget in (self.entry, self.repeat, self.choice, self.ok_button):
            widget.set_sensitive(not busy)
        self._update_hint()

    def _update_hint(self) -> None:
        if self.busy:
            parts = ["Checking…"]
        else:
            parts = [self.failure] if self.failure else []
            if self.caps and self.input.get_visible():
                parts.append("Caps Lock is on")
        self.hint.set_text(" · ".join(parts))
        self.hint.set_visible(bool(parts))
        flag(self.hint, "failed", bool(self.failure) and not self.busy)

    def _submit(self) -> None:
        if not self.ok_button.get_sensitive() or self.agent is None:
            return
        password = self.entry.get_text()
        if self.repeat_input.get_visible() and password != self.repeat.get_text():
            self.error("Passwords do not match")
            return
        self.failure = ""
        self._set_busy(True)
        flag(self.input, "failed", False)
        self.agent.respond(password, self.choice.get_active())
        for entry in (self.entry, self.repeat):
            entry.set_text("")


def build(context: Any) -> list[Any]:
    if mock.ENABLED:
        return [AuthWindow(context.monitors[0], context.keyboard)]
    windows = []
    for service in (polkit, keyring):
        window = AuthWindow(context.monitors[0], context.keyboard)
        window.agent = service.start(window)
        if window.agent is not None:
            windows.append(window)
    return windows
