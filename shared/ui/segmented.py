"""Segmented indicator: options side by side, the picked one under a gliding thumb."""

from __future__ import annotations

from typing import Any

from fabric.widgets.box import Box
from gi.repository import Gtk

from shared.ui.controls import Tween
from shared.widgets import flag, text


class Segmented(Box):
    """Equal segments with no seams; the picked one sits under a thumb that glides across on a
    change while the labels crossfade. The thumb is painted under the labels from CSS
    (.ui-segmented-thumb: the bar's focused-workspace fill), so a module restyles it there."""

    def __init__(self, options: tuple[str, ...], *classes: str):
        self.options = options
        self.items = [text(option, "ui-segment") for option in options]
        for label in self.items:
            label.set_hexpand(True)
        super().__init__(homogeneous=True, h_expand=True, style_classes=("ui-segmented", *classes), children=self.items)
        self.position = 0.0  # segment index, fractional mid-glide
        self.tween = Tween(self, lambda: self.position, self.move)
        self.connect("draw", self.paint)  # before the default handler: the thumb sits under the labels

    def move(self, position: float) -> None:
        self.position = position
        self.queue_draw()

    def select(self, option: str, animate: bool = True) -> None:
        """animate=False lands the thumb at once (initial state); unknown options leave it where it is."""
        for label, item in zip(self.items, self.options):
            flag(label, "active", item == option)
        if option in self.options:
            (self.tween.to if animate else self.tween.jump)(float(self.options.index(option)))

    def paint(self, _widget: Gtk.Widget, cr: Any) -> bool:
        width = self.get_allocated_width() / len(self.items)
        context = self.get_style_context()
        context.save()
        context.add_class("ui-segmented-thumb")
        Gtk.render_background(context, cr, self.position * width, 0, width, self.get_allocated_height())
        context.restore()
        return False
