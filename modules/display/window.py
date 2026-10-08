"""Display panel: brightness, contrast, gamma and warmth, with named presets. Dropped from the bar's brightness slot."""

from __future__ import annotations

from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry

from modules.display.logic import shown
from services.display import LIMITS, NEUTRAL, DisplayState
from services.monitors import Monitor
from services.system import BacklightState
from shared.ui import Confirm, Slider, big_value, button, check, header, icon_button, list_row, panel, row_list, slider
from shared.widgets import brightness_icon, flag, text, wrapped
from shared.window import BarPanel

LABELS = {"brightness": "Brightness", "contrast": "Contrast", "gamma": "Gamma", "warmth": "Warmth"}


class DisplayWindow(BarPanel):
    def __init__(self, monitor: Monitor, state: DisplayState, backlight: BacklightState):
        self.state = state
        self.backlight = backlight
        self.shown_presets: tuple[Any, ...] | None = None

        self.heading = big_value()
        self.edited = text("Edited", "ui-detail", "display-edited", xalign=0)
        self.reset_button = Button(label="Reset", style_classes=("display-link",), tooltip_text="Neutral contrast, gamma and warmth",
                            on_clicked=lambda *_: state.reset())
        self.scales: dict[str, Slider] = {}
        self.values: dict[str, Any] = {}
        self.rows: dict[str, Box] = {}
        for key in LIMITS:
            low, high = LIMITS[key]
            scale = slider(f"display-{key}", min_value=low, max_value=high, on_change=lambda _value: self.apply(),
                           increments=(100 if key == "warmth" else 1, 10), digits=0, has_origin=key == "brightness")
            self.scales[key] = scale
            self.values[key] = text("", "display-value")
            self.rows[key] = Box(orientation="v", children=[
                Box(children=[text(LABELS[key], "display-label"), Box(h_expand=True), self.values[key]]), scale])

        self.list = row_list()
        self.update_button = Button(label="Update", style_classes=("display-link",), on_clicked=lambda *_: self.store(state.value["active"]))
        self.empty = wrapped("Save the current look to switch back to it in one click.", "ui-detail", chars=36)
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

    def apply(self) -> None:
        values = {key: int(scale.get_value()) for key, scale in self.scales.items()}
        if self.backlight.value is not None and values["brightness"] != self.backlight.value:
            self.backlight.set(values.pop("brightness"))
        values.pop("brightness", None)
        self.state.adjust(**values)

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
        for key, scale in self.scales.items():
            scale.sync(settings[key])

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
        delete = icon_button("󰆴", None, "display-delete", tooltip=f"Delete {name}")  # Confirm owns the click
        row = list_row(
            brightness_icon(brightness),
            name,
            Box(spacing=6, children=[delete, check(selected)]),
            classes=("default",) if selected else (),
            on_clicked=lambda *_: self.state.apply(name),
            tooltip=f"Apply {name}" if not selected else f"Restore {name}",
        )
        # one click arms (red glyph), a second deletes; leaving the row or 3s disarms
        Confirm(delete, lambda: self.state.delete(name), lambda on: delete.set_tooltip_text("Click again to delete" if on else f"Delete {name}"), area=row)
        return row
