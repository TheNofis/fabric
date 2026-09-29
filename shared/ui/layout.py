"""Panel layout: the root container and section headers."""

from __future__ import annotations

from fabric.widgets.box import Box
from gi.repository import Gtk

from shared.widgets import text


def panel(*children: Gtk.Widget) -> Box:
    return Box(orientation="v", spacing=20, style_classes=("popup", "ui-panel"), children=children)


def header(title: str, end: Gtk.Widget | None = None) -> Box:
    """Section kicker: small caps title on the left, an optional widget (switch, detail) on the right."""
    return Box(children=[text(title.upper(), "ui-kicker", xalign=0), Box(h_expand=True), *((end,) if end else ())])
