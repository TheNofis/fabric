"""List rows: devices, networks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from gi.repository import Gtk, Pango

from shared.widgets import text


def row_list() -> Box:
    """Column of list_rows; bleeds into the panel padding so row fills line up with the text."""
    return Box(orientation="v", spacing=2, style_classes=("ui-rows",))


def list_row(icon: str, label: str, end: Gtk.Widget, *, classes: tuple[str, ...] = (), icon_classes: tuple[str, ...] = (),
             on_clicked: Callable[..., Any] | None = None, tooltip: str | None = None, max_chars: int = 26,
             on_menu: Callable[[], Any] | None = None) -> Button:
    """Device/network row: icon, ellipsized title, `end` on the right. No on_clicked = insensitive; on_menu = right click."""
    title = text(label, "ui-row-title", xalign=0)
    title.set_ellipsize(Pango.EllipsizeMode.END)
    title.set_max_width_chars(max_chars)
    button = Button(
        child=Box(spacing=10, children=[text(icon, "ui-row-icon", *icon_classes), title, Box(h_expand=True), end]),
        tooltip_text=tooltip,
        style_classes=("ui-row", *classes),
    )
    if on_clicked is None:
        button.set_sensitive(False)
    else:
        button.connect("clicked", on_clicked)
    if on_menu is not None:
        button.connect("button-press-event", lambda _widget, event: event.button == 3 and (on_menu() or True))
    button.connect_after("map", lambda *_: _lift(end))
    return button


def _lift(widget: Gtk.Widget) -> None:
    """GTK3 maps a button's input window above its children: raise the buttons inside `end`
    (delete, options) back on top, or the row takes their clicks."""
    if isinstance(widget, Gtk.Button) and widget.get_event_window():
        widget.get_event_window().raise_()
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            _lift(child)
