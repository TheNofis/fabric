"""Buttons shared by panels."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fabric.widgets.button import Button
from gi.repository import Gdk, GLib, Gtk

from shared.widgets import flag, text


def button(label: str, on_clicked: Callable[..., Any], *classes: str, primary: bool = False) -> Button:
    """Dialog/form button; primary = the accent-filled default action."""
    return Button(label=label, style_classes=("ui-button", *classes, *(("primary",) if primary else ())), on_clicked=on_clicked)


def icon_button(glyph: str, on_clicked: Callable[..., Any] | None, *classes: str, tooltip: str | None = None) -> Button:
    """Glyph button for the pointer only: it never takes focus, so the keyboard stays where it was."""
    widget = Button(style_classes=classes, child=text(glyph), tooltip_text=tooltip)
    if on_clicked is not None:  # None: the caller wires the click (Confirm)
        widget.connect("clicked", on_clicked)
    widget.set_can_focus(False)
    return widget


def nav_button(glyph: str, on_clicked: Callable[..., Any]) -> Button:
    """Month arrow."""
    return icon_button(glyph, on_clicked, "ui-nav", "ui-nav-arrow")


class Confirm:
    """Two clicks for a destructive action: the first arms `button` (flag "armed", on_arm(True)),
    the second runs on_confirm. Disarms after 3s or when the pointer leaves `area` (the button)."""

    def __init__(self, button: Gtk.Button, on_confirm: Callable[[], Any], on_arm: Callable[[bool], Any] | None = None, area: Gtk.Widget | None = None):
        self.button, self.on_arm = button, on_arm
        self.armed, self.timer = False, 0
        button.connect("clicked", lambda *_: on_confirm() if self.armed else self.arm(True))
        (area or button).connect("leave-notify-event", lambda _widget, event: event.detail != Gdk.NotifyType.INFERIOR and self.arm(False))
        button.connect("destroy", lambda *_: self.timer and GLib.source_remove(self.timer))

    def arm(self, on: bool) -> bool:
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add_seconds(3, self._expire) if on else 0
        self.armed = on
        flag(self.button, "armed", on)
        if self.on_arm:
            self.on_arm(on)
        return False

    def _expire(self) -> bool:
        self.timer = 0
        return self.arm(False)
