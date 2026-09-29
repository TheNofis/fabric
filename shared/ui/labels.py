"""Text pieces of a panel: the big number, its dimmed unit, the selection check."""

from __future__ import annotations

from fabric.widgets.label import Label

from shared.widgets import text


def big_value() -> Label:
    """Big light numeral heading a section; fill it with set_markup(amount(...)) or set_text."""
    return text("", "ui-big", xalign=0)


def amount(value: object, unit: str) -> str:
    """Big number with a smaller, dimmed unit: '6.2 GB'."""
    return f'{value}<span font_size="55%" fgalpha="64%"> {unit}</span>'


def check(on: bool = True) -> Label:
    """Accent check marking the selected row; empty when off so the row keeps its width."""
    return text("󰄬" if on else "", "ui-check")
