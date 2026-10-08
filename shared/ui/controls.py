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


class Tween:
    """Eases a value to a target on `widget`'s frame clock: ease-out cubic over 220ms (the "normal"
    motion token). Retargeting mid-way starts from where the value is; frames run only while mapped."""

    DURATION = 220_000  # µs

    def __init__(self, widget: Gtk.Widget, get: Callable[[], float], set: Callable[[float], Any]):
        self.widget, self.get, self.set = widget, get, set
        self.tick = self.start = 0
        self.origin = self.target = 0.0
        self.duration, self.linear = self.DURATION, False

    def to(self, target: float, duration: int = DURATION, linear: bool = False) -> None:
        """duration in µs; linear for chained segments, where easing each one would pulse."""
        self.origin, self.target, self.duration, self.linear = self.get(), target, duration, linear
        # start on the clock's "now", not the next tick: a restart every key repeat (~33ms) would
        # otherwise spend its first frame at t=0 and the thumb stutters stop-move-stop
        clock = self.widget.get_frame_clock()
        self.start = clock.get_frame_time() if clock else 0
        if not self.tick:
            self.tick = self.widget.add_tick_callback(self.step)

    def jump(self, value: float) -> None:
        if self.tick:
            self.widget.remove_tick_callback(self.tick)
            self.tick = 0
        self.set(value)

    def step(self, _widget: Gtk.Widget, clock: Any) -> bool:
        now = clock.get_frame_time()
        self.start = self.start or now
        t = min((now - self.start) / self.duration, 1.0)
        eased = t if self.linear else 1 - (1 - t) ** 3  # quick to move, soft to land
        self.set(self.origin + (self.target - self.origin) * eased)
        if t < 1:
            return True
        self.tick = 0
        return False


class Meter(Gtk.ProgressBar):
    """ProgressBar whose set_fraction glides to the new value while it is on screen."""

    def __init__(self, glide: bool, **kwargs: Any):
        super().__init__(**kwargs)
        self.glide = glide
        self.tween = Tween(self, self.get_fraction, lambda value: Gtk.ProgressBar.set_fraction(self, value))

    def set_fraction(self, fraction: float) -> None:
        (self.tween.to if self.glide and self.get_mapped() else self.tween.jump)(fraction)


def meter(*classes: str, glide: bool = True) -> Meter:
    """glide=False for bars driven every frame (countdowns), where easing would only lag."""
    return css(Meter(glide, hexpand=True, valign=Gtk.Align.CENTER), "ui-meter", *classes)
