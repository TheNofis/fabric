"""A selection thumb that glides between widgets, and the segmented indicator built on it."""

from __future__ import annotations

from typing import Any

from fabric.widgets.box import Box
from gi.repository import GLib, Gtk

from shared.ui.controls import Tween
from shared.widgets import flag, text

Rect = tuple[float, float, float, float]


class Glider:
    """Paints a CSS-styled thumb (`thumb_class`: background, box-shadow, radius) under `container`'s
    children and glides it to the picked one, position and size, instead of the fill jumping.

    The container must not draw a background of its own: GTK paints that after this handler and
    would cover the thumb, so wrap the children in a bare Box inside a styled one. Children may be
    rebuilt freely; the thumb follows the index, and a glide starts from where it was drawn.

    follow names a style class (selected, focused, default): the thumb then finds the child carrying
    it on every draw, so modules only flag their children and never call to()."""

    def __init__(self, container: Gtk.Widget, thumb_class: str, follow: str | None = None):
        self.container, self.thumb_class, self.follow = container, thumb_class, follow
        self.index: int | None = None
        self.origin: Rect | None = None
        self.progress = 1.0
        self.moved = 0  # µs, the last to(): its gap paces a chase
        self.tween = Tween(container, lambda: self.progress, self.step)
        container.connect("draw", self.paint)  # before the default handler: under the children

    def to(self, index: int | None, animate: bool = True) -> None:
        """index None hides the thumb; animate=False lands it at once (rebuilt lists, first show)."""
        current = self.rect()
        now, gliding = GLib.get_monotonic_time(), self.progress < 1
        gap, self.moved = now - self.moved, now
        self.index = index
        if animate and current is not None and index is not None:
            self.origin, self.progress = current, 0.0
            if gliding:
                # retarget mid-glide (held arrow, fast hover): a linear leg as long as the gap since the
                # last move, so legs chain at a steady speed and the thumb stays under a row behind;
                # restarting the 220ms ease every key repeat would leave it rows back
                self.tween.to(1.0, min(max(gap, 16_000), Tween.DURATION), linear=True)
            else:
                self.tween.to(1.0)
        else:
            self.origin = None
            self.tween.jump(1.0)

    def step(self, progress: float) -> None:
        self.progress = progress
        self.container.queue_draw()

    def target(self) -> Rect | None:
        children = self.container.get_children()
        if self.index is None or not 0 <= self.index < len(children) or not children[self.index].get_visible():
            return None
        child = children[self.index]
        if child.get_allocated_width() <= 1:
            return None  # never laid out yet
        x, y = child.translate_coordinates(self.container, 0, 0) or (0, 0)
        m = child.get_style_context().get_margin(child.get_state_flags())  # GTK3 allocations include CSS margin
        return x + m.left, y + m.top, child.get_allocated_width() - m.left - m.right, child.get_allocated_height() - m.top - m.bottom

    def rect(self) -> Rect | None:
        target = self.target()
        if target is None or self.origin is None or self.progress >= 1:
            return target
        return tuple(a + (b - a) * self.progress for a, b in zip(self.origin, target))  # type: ignore[return-value]

    def paint(self, _widget: Gtk.Widget, cr: Any) -> bool:
        if self.follow:
            children = self.container.get_children()
            index = next((i for i, child in enumerate(children) if child.get_style_context().has_class(self.follow)), None)
            if index != self.index:
                self.to(index, animate=self.index is not None and index is not None)
        rect = self.rect()
        if rect is not None and rect[2] > 0:
            context = self.container.get_style_context()
            context.save()
            context.add_class(self.thumb_class)
            Gtk.render_background(context, cr, *rect)
            context.restore()
        return False


class Segmented(Box):
    """Equal segments with no seams; the picked one sits under a Glider thumb
    (.ui-segmented-thumb: the bar's focused-workspace fill) while the labels crossfade."""

    def __init__(self, options: tuple[str, ...], *classes: str):
        self.options = options
        self.items = [text(option, "ui-segment") for option in options]
        for label in self.items:
            label.set_hexpand(True)
        super().__init__(homogeneous=True, h_expand=True, style_classes=("ui-segmented", *classes), children=self.items)
        self.glider = Glider(self, "ui-segmented-thumb")

    def select(self, option: str, animate: bool = True) -> None:
        """animate=False lands the thumb at once (initial state); unknown options leave it where it is."""
        for label, item in zip(self.items, self.options):
            flag(label, "active", item == option)
        if option in self.options:
            self.glider.to(self.options.index(option), animate)
