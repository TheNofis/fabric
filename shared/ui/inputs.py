"""Text inputs: an entry with its own placeholder, the masked password entry and the rounded field around it."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.entry import Entry
from fabric.widgets.overlay import Overlay
from gi.repository import Gtk

from shared.widgets import text


def password_entry(on_activate: Callable[[], Any]) -> Entry:
    entry = Entry(h_expand=True, style_classes=("ui-field-entry",))
    entry.set_visibility(False)  # Fabric's Entry ignores a visibility kwarg
    entry.set_invisible_char("●")
    entry.connect("activate", lambda *_: on_activate())
    return entry


class HintEntry(Overlay):
    """Entry with its own placeholder, shown while the entry is empty: GTK3 hides an Entry's
    placeholder while it has focus, and these entries always have it. .entry is the Entry."""

    def __init__(self, hint: str, *classes: str, hint_class: str = "ui-entry-hint"):
        self.entry = Entry(h_expand=True, style_classes=classes)
        self.hint = text(hint, hint_class, xalign=0)
        self.hint.set_no_show_all(True)  # a show_all() of the panel must not put it over typed text
        super().__init__(child=self.entry, overlays=[self.hint], h_expand=True)
        self.entry.connect("changed", lambda *_: self.hint.set_visible(not self.entry.get_text()))


def password_field(entry: Entry, *extra: Gtk.Widget, classes: tuple[str, ...] = ()) -> Box:
    """Lock icon + entry (+ extra widgets on the right, e.g. the keyboard layout)."""
    return Box(spacing=10, style_classes=("ui-field", *classes), children=[text("\U000F033E", "ui-field-icon"), entry, *extra])  # nf-md-lock
