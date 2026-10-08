"""Value controls: switch, slider, meter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fabric.widgets.scale import Scale
from gi.repository import Gtk

from shared.widgets import css


def switch(on_change: Callable[[Gtk.Switch, bool], bool], *classes: str) -> Gtk.Switch:
    """on_change(switch, active) -> True to keep the old state until the backend confirms."""
    widget = css(Gtk.Switch(valign=Gtk.Align.CENTER), "ui-switch", *classes)
    widget.connect("state-set", on_change)
    widget.show()  # plain Gtk widget: not visible by default like Fabric's
    return widget


def slider(*classes: str, max_value: int = 100, **kwargs: Any) -> Scale:
    kwargs.setdefault("h_expand", "size" not in kwargs)
    return Scale(min_value=0, max_value=max_value, style_classes=("ui-slider", *classes), **kwargs)


class Meter(Gtk.ProgressBar):
    """ProgressBar whose set_fraction glides to the new value while it is on screen."""

    DURATION = 220_000  # µs, the shell's "normal" motion token

    def __init__(self, glide: bool, **kwargs: Any):
        super().__init__(**kwargs)
        self.glide, self.tick, self.start = glide, 0, 0
        self.origin = self.target = 0.0

    def set_fraction(self, fraction: float) -> None:
        if not (self.glide and self.get_mapped()):
            self.stop()
            super().set_fraction(fraction)
            return
        self.origin, self.target, self.start = self.get_fraction(), fraction, 0  # retarget mid-glide from where it is
        if not self.tick:
            self.tick = self.add_tick_callback(self.step)

    def step(self, _widget: Gtk.Widget, clock: Any) -> bool:
        now = clock.get_frame_time()
        self.start = self.start or now
        t = min((now - self.start) / self.DURATION, 1.0)
        eased = 1 - (1 - t) ** 3  # ease-out cubic: quick to move, soft to land
        super().set_fraction(self.origin + (self.target - self.origin) * eased)
        if t < 1:
            return True
        self.tick = 0
        return False

    def stop(self) -> None:
        if self.tick:
            self.remove_tick_callback(self.tick)
            self.tick = 0


def meter(*classes: str, glide: bool = True) -> Meter:
    """glide=False for bars driven every frame (countdowns), where easing would only lag."""
    return css(Meter(glide, hexpand=True, valign=Gtk.Align.CENTER), "ui-meter", *classes)
