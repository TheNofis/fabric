"""Display panel: brightness, contrast, gamma and warmth, with named presets. Dropped from the bar's brightness slot."""

from __future__ import annotations

from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.scale import Scale
from gi.repository import Gdk, GLib

from services.display import LIMITS, NEUTRAL, DisplayState
from services.monitors import Monitor
from services.system import BacklightState
from shared.ui import big_value, button, check, header, list_row, panel, row_list
from shared.widgets import flag, text
from shared.window import BarPanel

LABELS = {"brightness": "Brightness", "contrast": "Contrast", "gamma": "Gamma", "warmth": "Warmth"}


def shown(key: str, value: int) -> str:
    if key == "gamma":
        return f"{value / 100:.2f}"
    if key == "warmth":
        return "Neutral" if value >= 6500 else f"{value}K"
    return f"{value}%"


def sun(brightness: int) -> str:
    return "󰃞" if brightness < 34 else "󰃟" if brightness < 67 else "󰃠"


class DisplayWindow(BarPanel):
    def __init__(self, monitor: Monitor, state: DisplayState, backlight: BacklightState):
        self.state = state
        self.backlight = backlight
        self.pending = 0
        self.syncing = False
        self.shown_presets: tuple[Any, ...] | None = None

        self.heading = big_value()
        self.edited = text("Edited", "ui-detail", "display-edited", xalign=0)
        self.reset_button = Button(label="Reset", style_classes=("display-link",), tooltip_text="Neutral contrast, gamma and warmth",
                            on_clicked=lambda *_: state.reset())
        self.scales: dict[str, Scale] = {}
        self.values: dict[str, Any] = {}
        self.rows: dict[str, Box] = {}
        for key in LIMITS:
            low, high = LIMITS[key]
            scale = Scale(min_value=low, max_value=high, increments=(100 if key == "warmth" else 1, 10), digits=0,
                          has_origin=key == "brightness", h_expand=True, style_classes=("ui-slider", f"display-{key}"))
            scale.connect("value-changed", self.on_drag)
            self.scales[key] = scale
            self.values[key] = text("", "display-value")
            self.rows[key] = Box(orientation="v", children=[
                Box(children=[text(LABELS[key], "display-label"), Box(h_expand=True), self.values[key]]), scale])

        self.list = row_list()
        self.update_button = Button(label="Update", style_classes=("display-link",), on_clicked=lambda *_: self.store(state.value["active"]))
        self.empty = text("Save the current look to switch back to it in one click.", "ui-detail", xalign=0)
        self.empty.set_line_wrap(True)
        self.empty.set_max_width_chars(36)
        self.name_entry = Entry(placeholder="New preset", max_length=24, h_expand=True, style_classes=("ui-field-entry", "display-name"))
        self.name_entry.connect("activate", lambda *_: self.store(self.name_entry.get_text()))
        self.name_entry.connect("changed", lambda *_: self.sync_save())
        # override-redirect popups never get X focus from i3; without it the entry ignores keys
        self.name_entry.connect("button-press-event", lambda *_: self.take_focus() or False)
        self.save_button = button("Save", lambda *_: self.store(self.name_entry.get_text()), primary=True)
        for widget in (self.edited, self.reset_button, self.update_button, self.empty, self.rows["brightness"]):
            widget.set_no_show_all(True)

        super().__init__(monitor, "display", panel(
            Box(orientation="v", spacing=10, children=[
                Box(orientation="v", children=[
                    header("Display", self.reset_button),
                    Box(spacing=10, children=[self.heading, self.edited]),
                ]),
                *self.rows.values(),
            ]),
            Box(orientation="v", spacing=6, children=[
                header("Presets", self.update_button),
                self.list,
                self.empty,
                Box(spacing=8, style_classes=("ui-field", "display-form"), children=[self.name_entry, self.save_button]),
            ]),
        ))
        self.sync_save()
        state.subscribe(lambda _value: self.render())
        backlight.subscribe(lambda _value: self.render())

    # Dragging emits dozens of value-changed per second: apply at most every 50ms, with the latest positions.
    def on_drag(self, _scale: Scale) -> None:
        if not self.syncing and not self.pending:
            self.pending = GLib.timeout_add(50, self.apply)

    def apply(self) -> bool:
        self.pending = 0
        values = {key: int(scale.get_value()) for key, scale in self.scales.items()}
        if self.backlight.value is not None and values["brightness"] != self.backlight.value:
            self.backlight.set(values.pop("brightness"))
        values.pop("brightness", None)
        self.state.adjust(**values)
        return False

    def store(self, name: str) -> None:
        self.state.store(name)
        self.name_entry.set_text("")

    def sync_save(self) -> None:
        name = self.name_entry.get_text().strip()
        self.save_button.set_sensitive(bool(name))
        self.save_button.set_label("Replace" if self.state.preset(name) else "Save")

    def render(self) -> None:
        value, brightness = self.state.value, self.backlight.value
        settings = {**value["settings"], "brightness": brightness or 0}
        for key, scale in self.scales.items():
            self.values[key].set_text(shown(key, settings[key]))
        self.rows["brightness"].set_visible(brightness is not None)
        if not self.pending:  # the user is dragging; don't yank the sliders back to stale values
            self.syncing = True
            for key, scale in self.scales.items():
                scale.set_value(settings[key])
            self.syncing = False

        active, edited = value["active"] if self.state.preset(value["active"]) else "", self.state.modified()
        self.heading.set_text(active or "Custom")
        flag(self.heading, "dim", not active)
        self.edited.set_visible(edited)
        self.update_button.set_visible(edited)
        self.update_button.set_tooltip_text(f"Save the current look into {active}" if edited else None)
        self.reset_button.set_visible(value["settings"] != NEUTRAL)
        self.empty.set_visible(not value["presets"])
        self.sync_save()

        # rebuild the rows only when the presets or the selection change, not on every slider step
        presets = tuple((p["name"], p["brightness"], p["name"] == active) for p in value["presets"])
        if presets == self.shown_presets:
            return
        self.shown_presets = presets
        self.list.children = [self.row(*entry) for entry in presets]
        self.list.show_all()

    def row(self, name: str, brightness: int, selected: bool) -> Button:
        # one click arms (red glyph), a second deletes; leaving the row or 3s disarms
        delete = Button(label="󰆴", style_classes=("display-delete",), tooltip_text=f"Delete {name}",
                        on_clicked=lambda *_: self.state.delete(name) if delete.get_style_context().has_class("armed") else arm(True))
        delete.set_can_focus(False)
        timer = [0]

        def arm(on: bool) -> bool:
            if timer[0]:
                GLib.source_remove(timer[0])
            timer[0] = GLib.timeout_add_seconds(3, expire) if on else 0
            flag(delete, "armed", on)
            delete.set_tooltip_text("Click again to delete" if on else f"Delete {name}")
            return False

        def expire() -> bool:
            timer[0] = 0
            return arm(False)

        delete.connect("destroy", lambda *_: timer[0] and GLib.source_remove(timer[0]))
        row = list_row(
            sun(brightness),
            name,
            Box(spacing=6, children=[delete, check(selected)]),
            classes=("default",) if selected else (),
            on_clicked=lambda *_: self.state.apply(name),
            tooltip=f"Apply {name}" if not selected else f"Restore {name}",
        )
        row.connect("leave-notify-event", lambda _row, event: event.detail != Gdk.NotifyType.INFERIOR and arm(False))
        return row


def build(context: Any) -> list[Any]:
    state = DisplayState(context.backlight)
    context.displays = [DisplayWindow(monitor, state, context.backlight) for monitor in context.monitors]
    return context.displays
