"""Buttons shared by panels."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fabric.widgets.button import Button

from shared.widgets import text


def button(label: str, on_clicked: Callable[..., Any], *classes: str, primary: bool = False) -> Button:
    """Dialog/form button; primary = the accent-filled default action."""
    return Button(label=label, style_classes=("ui-button", *classes, *(("primary",) if primary else ())), on_clicked=on_clicked)


def nav_button(glyph: str, on_clicked: Callable[..., Any]) -> Button:
    """Month arrow; not focusable so the keyboard stays where it was."""
    button = Button(style_classes=("ui-nav", "ui-nav-arrow"), child=text(glyph), on_clicked=on_clicked)
    button.set_can_focus(False)
    return button
