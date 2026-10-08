"""Panel layout: the root container, section headers and stat sections."""

from __future__ import annotations

from fabric.widgets.box import Box
from gi.repository import Gtk

from shared.widgets import text


def panel(*children: Gtk.Widget) -> Box:
    return Box(orientation="v", spacing=20, style_classes=("popup", "ui-panel"), children=children)


def header(title: str, end: Gtk.Widget | None = None) -> Box:
    """Section kicker: small caps title on the left, an optional widget (switch, detail) on the right."""
    return Box(children=[text(title.upper(), "ui-kicker", xalign=0), Box(h_expand=True), *((end,) if end else ())])


def section(kicker: str, detail: Gtk.Widget, value: Gtk.Label, meta: list[Gtk.Widget], *rows: Gtk.Widget) -> Box:
    """Stat section (system monitor, Claude limits): header, the big value with small meta values
    on its baseline at the right, then meters and detail rows."""
    return Box(
        orientation="v",
        spacing=6,
        children=[
            header(kicker, detail),
            Box(children=[value, Box(h_expand=True), Box(spacing=12, valign="end", style_classes=("ui-stat-meta",), children=meta)]),
            *rows,
        ],
    )


def detail_row(label: str, value: Gtk.Label, bar: Gtk.Widget | None = None) -> Box:
    """Secondary line of a section: label, value at the right, an optional thin meter below."""
    return Box(
        orientation="v",
        spacing=5,
        style_classes=("ui-stat-row",),
        children=[Box(children=[text(label, "ui-stat-label"), Box(h_expand=True), value]), *((bar,) if bar else ())],
    )


def stat_value(value: str = "") -> Gtk.Label:
    """Small value in a section's meta or a detail row; flag "alert" when it is in trouble."""
    return text(value, "ui-stat-value")
