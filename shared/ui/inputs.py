"""Text inputs: masked password entry and the rounded field around it."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.entry import Entry
from gi.repository import Gtk

from shared.widgets import text


def password_entry(on_activate: Callable[[], Any]) -> Entry:
    entry = Entry(h_expand=True, style_classes=("ui-field-entry",))
    entry.set_visibility(False)  # Fabric's Entry ignores a visibility kwarg
    entry.set_invisible_char("●")
    entry.connect("activate", lambda *_: on_activate())
    return entry


def password_field(entry: Entry, *extra: Gtk.Widget, classes: tuple[str, ...] = ()) -> Box:
    """Lock icon + entry (+ extra widgets on the right, e.g. the keyboard layout)."""
    return Box(spacing=10, style_classes=("ui-field", *classes), children=[text("\U000F033E", "ui-field-icon"), entry, *extra])  # nf-md-lock
