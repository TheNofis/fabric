"""Status bar: system stats, clock, workspaces, keyboard, network, brightness, volume."""

from __future__ import annotations

from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.scale import Scale
from fabric.system_tray.widgets import get_tray_watcher
from gi.repository import Gdk, GLib, Gtk

from modules.calendar import CalendarWindow
from modules.claude import ClaudeWindow, claude_slot
from modules.display import DisplayWindow
from modules.network import NetworkWindow
from modules.sound import SoundWindow
from modules.sysmon import SystemMonitorWindow
from services import mock
from services.monitors import Monitor
from services.state import JsonState
from services.system import BacklightState, ClockState, KeyboardState, NetworkState, SystemState
from shared.constants import BAR_HEIGHT
from shared.ui import slider
from shared.widgets import flag, hover_reveal, island, run, scroll_up, slide, stat, text, toggle_mute, volume_icon, volume_text
from shared.window import MonitorWindow


class WorkspacesView(EventBox):
    def __init__(self, monitor: str, state: JsonState):
        self.monitor = monitor
        self.shown: list[dict[str, Any]] | None = None
        self.row = Box(spacing=2, style_classes=("island", "workspaces"))
        super().__init__(
            events="scroll",
            child=self.row,
            on_scroll_event=self.on_scroll,
        )
        state.subscribe(self.update)

    def update(self, workspaces: list[dict[str, Any]]) -> None:
        # the stream carries every output; events on another monitor must not rebuild this row
        mine = [workspace for workspace in workspaces if workspace.get("output") == self.monitor]
        if mine == self.shown:
            return
        self.shown = mine
        buttons = []
        for workspace in mine:
            classes = ["ws"]
            classes += [name for name in ("focused", "visible", "urgent") if workspace.get(name)]
            name = str(workspace.get("name", ""))
            buttons.append(
                Button(
                    label=name,
                    style_classes=classes,
                    on_clicked=lambda _button, workspace_name=name: run(
                        "i3-msg", "-q", "workspace", workspace_name
                    ),
                )
            )
        self.row.children = buttons
        self.row.show_all()

    @staticmethod
    def on_scroll(_widget: Gtk.Widget, event: Gdk.EventScroll) -> bool:
        command = "prev_on_output" if scroll_up(event) else "next_on_output"
        run("i3-msg", "-q", "workspace", command)
        return True

class Tray(Box):
    """StatusNotifierItem tray on Fabric's watcher. Passive items stay hidden (the spec allows it),
    an empty tray takes no room. Menus drop from the icon, like macOS menu extras."""

    def __init__(self):
        super().__init__(spacing=2, style_classes=("tray",))
        self.set_no_show_all(True)  # the bar's show_all() must not reveal passive items
        self.buttons: dict[str, Button] = {}
        if mock.ENABLED:
            for glyph, title in (("󰊤", "GitHub"), ("󰋋", "Studio Headset"), ("󰄗", "Clipboard")):
                self.add(Button(child=text(glyph, "icon"), style_classes=("bar-button", "tray-item"), tooltip_text=title))
            self.show_all()
            return
        self.watcher = get_tray_watcher()
        self.watcher.connect("item-added", lambda _watcher, key: self.add_item(key))
        self.watcher.connect("item-removed", lambda _watcher, key: self.remove_item(key))
        for key in list(self.watcher.items):
            self.add_item(key)

    def add_item(self, key: str) -> None:
        item = self.watcher.items.get(key)
        if item is None or key in self.buttons:
            return
        image = Image()
        image.show()
        button = Button(child=image, style_classes=("bar-button", "tray-item"))
        button.add_events(Gdk.EventMask.SCROLL_MASK)
        button.connect("button-press-event", lambda widget, event: self.on_press(widget, item, event))
        button.connect("scroll-event", lambda _widget, event: item.scroll_for_event(event) or True)

        def update(*_: Any) -> None:
            try:
                pixbuf = item.get_preferred_icon_pixbuf(16, "hyper")
            except GLib.Error:
                pixbuf = None
            if pixbuf is None:
                image.set_from_icon_name("application-x-executable", Gtk.IconSize.MENU)
            else:
                image.set_from_pixbuf(pixbuf)
            button.set_tooltip_text(item.tooltip.title or item.title or None)
            button.set_visible(item.status != "Passive")
            self.set_visible(any(other.get_visible() for other in self.buttons.values()))

        self.buttons[key] = button
        self.add(button)
        item.changed.connect(update)
        update()

    def remove_item(self, key: str) -> None:
        button = self.buttons.pop(key, None)
        if button is not None:
            button.destroy()
            self.set_visible(any(other.get_visible() for other in self.buttons.values()))

    @staticmethod
    def on_press(button: Button, item: Any, event: Gdk.EventButton) -> bool:
        x, y = int(event.x_root), int(event.y_root)
        try:
            if event.button == 1 and not item.is_menu:
                item.activate(x, y)
                return True
            if event.button == 2:
                item.secondary_activate(x, y)
                return True
        except GLib.Error:
            pass  # no Activate method (nm-applet and friends): fall back to the menu
        menu = item.menu
        if menu is None:
            item.context_menu(x, y)
            return True
        menu.set_property("rect-anchor-dy", 8)
        menu.popup_at_widget(button, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, event)
        return True


class Bar(MonitorWindow):
    def __init__(
        self,
        monitor: Monitor,
        clock: ClockState,
        system: SystemState,
        workspaces: JsonState,
        audio: JsonState,
        network: NetworkState,
        keyboard: KeyboardState,
        calendar: CalendarWindow,
        sysmon: SystemMonitorWindow,
        sound: SoundWindow,
        network_panel: NetworkWindow,
        claude_usage: JsonState,
        claude_panel: ClaudeWindow,
        backlight: BacklightState,
        display: DisplayWindow,
    ):
        temp = text("0°", "value")
        temp_stat = stat("󰔏", temp)
        cpu = text("0%", "value", "w-pct")
        used = text("0.0G", "value")
        total = text("", "muted")
        memory_revealer = slide(total, "right")
        memory = hover_reveal(stat("󰍛", used, memory_revealer), memory_revealer)

        date = text("", "muted")
        time = text("", "time")
        clock_widget = EventBox(
            events="button-press",
            child=Box(spacing=8, style_classes=("clock",), children=[date, time]),
            on_button_press_event=lambda widget, *_: calendar.toggle_at(widget) or True,
        )

        stats = EventBox(
            events="button-press",
            child=Box(spacing=18, children=[temp_stat, stat("󰓅", cpu), memory]),
            on_button_press_event=lambda widget, *_: sysmon.toggle_at(widget) or True,
        )
        left = island(stats, claude_slot(claude_usage, clock, claude_panel), clock_widget)

        layout = text("us", "value")
        caps = text("caps", "caps")

        net_detail = text("", "muted")
        net_revealer = slide(net_detail, "left")
        net_icon = text("󰈂", "icon")
        down = text("", "value", "w-speed")
        up = text("", "value", "w-speed")
        offline = text("offline", "value")
        speeds = Box(
            spacing=10,
            children=[
                Box(spacing=3, children=[text("↓", "arrow"), down]),
                Box(spacing=3, children=[text("↑", "arrow"), up]),
            ],
        )
        network_stat = Box(
            spacing=7,
            style_classes=("stat",),
            children=[net_icon, net_revealer, offline, speeds],
        )
        network_widget = hover_reveal(
            network_stat,
            net_revealer,
            events=("button-press",),
            on_button_press_event=lambda _widget, event: network_panel.toggle_at(network_stat) or True if event.button == 1 else False,
        )

        volume_label = text("0%", "value", "w-pct")
        mute_button = Button(label="󰕾", style_classes=("icon", "bar-button"), tooltip_text="Toggle mute", on_clicked=toggle_mute)
        volume_scale = slider("vol-slider", max_value=101, size=(88, -1))
        syncing = {"value": False}
        pending = {"source": 0}

        # Dragging emits dozens of value-changed per second: apply at most every 50ms,
        # always with the latest slider position.
        def apply_volume() -> bool:
            pending["source"] = 0
            run("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{min(int(volume_scale.value), 100)}%")
            return False

        def set_volume(_scale: Scale) -> None:
            if not syncing["value"] and not pending["source"]:
                pending["source"] = GLib.timeout_add(50, apply_volume)

        volume_scale.connect("value-changed", set_volume)
        volume_revealer = slide(volume_scale, "left")
        volume_stat = Box(
            spacing=7,
            style_classes=("stat",),
            children=[volume_revealer, mute_button, volume_label],
        )

        def scroll_volume(_widget: Gtk.Widget, event: Gdk.EventScroll) -> bool:
            # same cap as ~/.config/i3/scripts/volume.sh (200%)
            run("wpctl", "set-volume", "-l", "2", "@DEFAULT_AUDIO_SINK@", "5%+" if scroll_up(event) else "5%-")
            return True

        volume_widget = hover_reveal(
            volume_stat,
            volume_revealer,
            events=("scroll", "button-press"),
            on_scroll_event=scroll_volume,
            on_button_press_event=lambda _widget, event: (
                sound.toggle_at(volume_stat) or True if event.button == 1
                else run("pavucontrol") or True if event.button == 3 else False
            ),
        )
        brightness_label = text("0%", "value", "w-pct")
        brightness_icon = text("󰃠", "icon")
        brightness_scale = slider("vol-slider", max_value=101, size=(88, -1))
        brightness_syncing = {"value": False}
        brightness_pending = {"source": 0}

        def apply_brightness() -> bool:
            brightness_pending["source"] = 0
            backlight.set(int(brightness_scale.value))
            return False

        def drag_brightness(_scale: Scale) -> None:
            if not brightness_syncing["value"] and not brightness_pending["source"]:
                brightness_pending["source"] = GLib.timeout_add(50, apply_brightness)

        brightness_scale.connect("value-changed", drag_brightness)
        brightness_revealer = slide(brightness_scale, "left")
        brightness_stat = Box(spacing=7, style_classes=("stat",), children=[brightness_revealer, brightness_icon, brightness_label])
        brightness_widget = hover_reveal(
            brightness_stat,
            brightness_revealer,
            events=("scroll", "button-press"),
            on_scroll_event=lambda _widget, event: backlight.set((backlight.value or 0) + (5 if scroll_up(event) else -5)) or True,
            on_button_press_event=lambda _widget, event: display.toggle_at(brightness_stat) or True if event.button == 1 else False,
        )

        right = island(
            Tray(),
            stat("󰌌", layout, caps),
            network_widget,
            brightness_widget,
            volume_widget,
        )

        workspaces_view = WorkspacesView(monitor.name, workspaces)
        content = CenterBox(
            name="bar",
            start_children=left,
            center_children=workspaces_view,
            end_children=right,
        )
        super().__init__(
            monitor,
            title=f"fabric-bar-{monitor.name}",
            type_hint="dock",
            geometry="top-left",
            focusable=False,
            size=(monitor.width, BAR_HEIGHT),
            visible=False,
            child=content,
        )

        def update_system(value: dict[str, float]) -> None:
            temp.set_text(f"{value['temp']:.0f}°")
            flag(temp_stat, "alert", value["temp"] >= 80)
            cpu.set_text(f"{value['cpu']:.0f}%")
            used.set_text(f"{value['used'] / 1073741824:.1f}G")
            total.set_text(f"of {value['total'] / 1073741824:.0f}G")

        def update_audio(value: dict[str, Any]) -> None:
            muted = bool(value.get("muted"))
            volume = int(value.get("vol", 0))
            mute_button.set_label(volume_icon(volume, muted))
            volume_label.set_text(volume_text(volume, muted))
            flag(volume_stat, "alert", muted)
            if pending["source"]:
                return  # user is dragging; don't yank the slider back to a stale value
            syncing["value"] = True
            volume_scale.value = volume
            syncing["value"] = False

        def update_network(value: dict[str, Any]) -> None:
            interface = str(value.get("iface", ""))
            net_icon.set_text("󰈂" if not interface else "󰖩" if interface.startswith("w") else "󰈀")
            net_detail.set_text(f"{value.get('ip', '')}  {interface}  ")
            offline.set_visible(not interface)
            speeds.set_visible(bool(interface))
            down.set_text(str(value.get("down", "")))
            up.set_text(str(value.get("up", "")))
            flag(network_stat, "alert", not interface)

        def update_brightness(value: int | None) -> None:
            brightness_widget.set_visible(value is not None)
            if value is None:
                return
            brightness_icon.set_text("󰃞" if value < 34 else "󰃟" if value < 67 else "󰃠")
            brightness_label.set_text(f"{value}%")
            if brightness_pending["source"]:
                return  # mid-drag; keep the slider where the pointer is
            brightness_syncing["value"] = True
            brightness_scale.value = value
            brightness_syncing["value"] = False

        def update_keyboard(value: dict[str, Any]) -> None:
            layout.set_text(str(value.get("layout", "us")))
            caps.set_visible(bool(value.get("caps")))

        self.clip_to(12, left, workspaces_view.row, right)
        self.show_all()
        clock.subscribe(lambda now: (date.set_text(now.strftime("%a %d %b")), time.set_text(now.strftime("%H:%M"))))
        system.subscribe(update_system)
        audio.subscribe(update_audio)
        network.subscribe(update_network)
        keyboard.subscribe(update_keyboard)
        backlight.subscribe(update_brightness)


def build(context: Any) -> list[Any]:
    context.bars = [
        Bar(
            monitor,
            context.clock,
            context.system,
            context.workspaces,
            context.audio,
            context.network,
            context.keyboard,
            calendar,
            sysmon,
            sound,
            network_panel,
            context.claude,
            claude_panel,
            context.backlight,
            display,
        )
        for monitor, calendar, sysmon, sound, network_panel, claude_panel, display in zip(
            context.monitors, context.calendars, context.sysmons, context.sounds, context.network_panels, context.claude_panels, context.displays
        )
    ]
    return context.bars
