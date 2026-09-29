"""Value controls: switch, slider, meter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fabric.widgets.scale import Scale
from gi.repository import Gtk

from shared.widgets import css


def switch(on_change: Callable[[Gtk.Switch, bool], bool], *classes: str) -> Gtk.Switch:
    """on_change(switch, active) -> True to keep the old state until the backend confirms."""
    widget = css(Gtk.Switch(valign=Gtk.Align.CENTER), *classes)
    widget.connect("state-set", on_change)
    widget.show()  # plain Gtk widget: not visible by default like Fabric's
    return widget


def slider(*classes: str, max_value: int = 100, **kwargs: Any) -> Scale:
    kwargs.setdefault("h_expand", "size" not in kwargs)
    return Scale(min_value=0, max_value=max_value, style_classes=classes, **kwargs)


def meter(*classes: str) -> Gtk.ProgressBar:
    return css(Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER), "ui-meter", *classes)
