"""Status bar: system stats, clock, workspaces, keyboard, network, brightness, volume, notifications."""

from __future__ import annotations

from typing import Any, Callable

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.system_tray.widgets import get_tray_watcher
from gi.repository import Gdk, GLib, Gtk

from services import mock
from services.monitors import Monitor
from services.state import JsonState
from services.system import BacklightState, ClockState, KeyboardState, NetworkState, SystemState
from shared.constants import BAR_HEIGHT, GIB, HOT
from shared.ui import Glider, Slider, slider
from shared.widgets import brightness_icon, flag, hover_reveal, island, run, scroll_up, slide, stat, text, toggle_mute, volume_icon, volume_text
from shared.window import BarPanel, MonitorWindow


class WorkspacesView(EventBox):
    def __init__(self, monitor: str, state: JsonState):
        self.monitor = monitor
        self.shown: list[dict[str, Any]] | None = None
        # buttons sit in a bare box inside the island: the focused thumb is painted under them
        self.buttons = Box(spacing=2)
        self.row = Box(style_classes=("island", "workspaces"), children=[self.buttons])
        Glider(self.buttons, "ws-thumb", follow="focused")
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
        self.buttons.children = buttons
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


def slider_stat(glyph: str, scale: Slider, on_scroll: Callable[[Gdk.EventScroll], Any],
                on_press: Callable[[Box, Gdk.EventButton], bool], **kwargs: Any) -> tuple[EventBox, Box, Label, Label]:
    """Slot with an icon and a percent whose slider slides out on hover (volume, brightness).
    on_press gets the stat box, which panels align under. -> (slot, stat box, icon, percent)"""
    icon, value = text(glyph, "icon"), text("0%", "value", "w-pct")
    revealer = slide(scale, "left")
    stat_box = Box(spacing=7, style_classes=("stat",), children=[revealer, icon, value])
    slot = hover_reveal(
        stat_box,
        revealer,
        events=("scroll", "button-press"),
        on_scroll_event=lambda _widget, event: on_scroll(event) or True,
        on_button_press_event=lambda _widget, event: on_press(stat_box, event),
        **kwargs,
    )
    return slot, stat_box, icon, value


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
        calendar: BarPanel,
        sysmon: BarPanel,
        sound: BarPanel,
        network_panel: BarPanel,
        claude: Gtk.Widget,  # the Claude module's own slot
        backlight: BacklightState,
        display: BarPanel,
        notifications: Callable[[], Any],
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
        left = island(stats, claude, clock_widget)

        layout = text("US", "value")
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

        # sliders stop at 100%; scroll and the i3 keys boost to 200%, shown as .warn
        volume_scale = slider("vol-slider", size=(88, -1), on_change=lambda value: run("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{value}%"))

        def scroll_volume(event: Gdk.EventScroll) -> None:
            # same cap as ~/.config/i3/scripts/volume.sh (200%)
            run("wpctl", "set-volume", "-l", "2", "@DEFAULT_AUDIO_SINK@", "5%+" if scroll_up(event) else "5%-")

        volume_widget, volume_stat, volume_icon_label, volume_label = slider_stat(
            "󰕾",
            volume_scale,
            scroll_volume,
            lambda stat_box, event: (
                sound.toggle_at(stat_box) or True if event.button == 1
                else toggle_mute() or True if event.button == 2
                else run("pavucontrol") or True if event.button == 3 else False
            ),
            tooltip_text="Middle-click to mute",
        )
        brightness_scale = slider("vol-slider", max_value=101, size=(88, -1), on_change=backlight.set)
        brightness_widget, _, brightness_icon_label, brightness_label = slider_stat(
            "󰃠",
            brightness_scale,
            lambda event: backlight.set((backlight.value or 0) + (5 if scroll_up(event) else -5)),
            lambda stat_box, event: display.toggle_at(stat_box) or True if event.button == 1 else False,
        )

        bell_label = text("󰂚", "icon")
        bell = Button(
            child=bell_label,
            style_classes=("bar-button",),
            tooltip_text="Notifications",
            on_clicked=lambda *_: notifications() and notifications().toggle_center(),
        )

        def refresh_bell(*_: Any) -> bool:
            bell_label.set_text("󰂛" if getattr(notifications(), "dnd", False) else "󰂚")
            return False

        def attach_bell() -> bool:
            # the hub is built after the bar; DnD only changes inside the center, so re-read on close
            center = getattr(notifications(), "center_window", None)
            if center is not None:
                center.connect("hide", refresh_bell)
            return refresh_bell()

        right = island(
            Tray(),
            stat("󰌌", layout, caps),
            network_widget,
            brightness_widget,
            volume_widget,
            bell,
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
            flag(temp_stat, "alert", value["temp"] >= HOT)
            cpu.set_text(f"{value['cpu']:.0f}%")
            used.set_text(f"{value['used'] / GIB:.1f}G")
            total.set_text(f"of {value['total'] / GIB:.0f}G")

        def update_audio(value: dict[str, Any]) -> None:
            muted = bool(value.get("muted"))
            volume = int(value.get("vol", 0))
            volume_icon_label.set_text(volume_icon(volume, muted))
            volume_label.set_text(volume_text(volume, muted))
            flag(volume_stat, "alert", muted)
            flag(volume_stat, "warn", volume > 100 and not muted)
            volume_scale.sync(volume)

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
            brightness_icon_label.set_text(brightness_icon(value))
            brightness_label.set_text(f"{value}%")
            brightness_scale.sync(value)

        def update_keyboard(value: dict[str, Any]) -> None:
            layout.set_text(str(value.get("layout", "us")).upper())
            caps.set_visible(bool(value.get("caps")))

        self.clip_to(12, left, workspaces_view.row, right)
        self.show_all()
        clock.subscribe(lambda now: (date.set_text(now.strftime("%a %d %b")), time.set_text(now.strftime("%H:%M"))))
        system.subscribe(update_system)
        audio.subscribe(update_audio)
        network.subscribe(update_network)
        keyboard.subscribe(update_keyboard)
        backlight.subscribe(update_brightness)
        GLib.idle_add(attach_bell)
