"""Small widget helpers and commands reused across modules."""

from __future__ import annotations

import sys
from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from gi.repository import Gdk, GLib, Gtk


def run(*args: str) -> None:
    # GLib double-forks without DO_NOT_REAP_CHILD, so no zombies are left behind.
    try:
        GLib.spawn_async(
            list(args),
            flags=GLib.SpawnFlags.SEARCH_PATH
            | GLib.SpawnFlags.STDOUT_TO_DEV_NULL
            | GLib.SpawnFlags.STDERR_TO_DEV_NULL,
            # PyGObject's override adds CHILD_INHERITS_* unless these are truthy,
            # which clashes with *_TO_DEV_NULL and fails an assertion.
            standard_output=True,
            standard_error=True,
        )
    except GLib.Error as error:
        print(f"run {args[0]}: {error.message}", file=sys.stderr)


def copy_text(value: str) -> None:
    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(value, -1)


def scroll_up(event: Gdk.EventScroll) -> bool:
    return event.direction == Gdk.ScrollDirection.UP


def css(widget: Gtk.Widget, *classes: str) -> Gtk.Widget:
    for name in classes:
        widget.get_style_context().add_class(name)
    return widget


def flag(widget: Gtk.Widget, name: str, enabled: bool) -> None:
    context = widget.get_style_context()
    (context.add_class if enabled else context.remove_class)(name)


def text(value: str = "", *classes: str, xalign: float | None = None) -> Label:
    label = Label(label=value, style_classes=classes)
    if xalign is not None:
        label.set_xalign(xalign)
    return label


def toggle_mute(*_: Any) -> None:
    run("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle")


def volume_icon(volume: int, muted: bool) -> str:
    return "󰖁" if muted else "󰕿" if volume < 34 else "󰖀" if volume < 67 else "󰕾"


def volume_text(volume: int, muted: bool) -> str:
    return "off" if muted else f"{volume}%"


def hover_reveal(child: Gtk.Widget, revealer: Revealer, **kwargs: Any) -> EventBox:
    """EventBox that slides `revealer` open while the pointer is over `child`."""
    events = ("enter-notify", "leave-notify", *kwargs.pop("events", ()))
    pending = {"source": 0}

    def cancel() -> None:
        if pending["source"]:
            GLib.source_remove(pending["source"])
            pending["source"] = 0

    def close() -> bool:
        pending["source"] = 0
        revealer.unreveal()
        return False

    def on_enter(*_: Any) -> bool:
        cancel()
        revealer.reveal()
        return False

    def on_leave(_widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        # Moving onto a child with its own GdkWindow (Button, Scale) is not leaving.
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        # Delay the close: the revealer resizes the island and reshapes the window,
        # which can briefly push the pointer out and back in (open/close flicker).
        cancel()
        pending["source"] = GLib.timeout_add(200, close)
        return False

    return EventBox(
        events=events,
        child=child,
        on_enter_notify_event=on_enter,
        on_leave_notify_event=on_leave,
        **kwargs,
    )


def slide(child: Gtk.Widget, direction: str) -> Revealer:
    return Revealer(child=child, transition_type=f"slide-{direction}", transition_duration=160)


def stat(icon: str, *children: Gtk.Widget) -> Box:
    return Box(
        spacing=7,
        style_classes=("stat",),
        children=[text(icon, "icon"), *children],
    )


def island(*children: Gtk.Widget, classes: tuple[str, ...] = ()) -> Box:
    return Box(spacing=18, style_classes=("island", *classes), children=children)
