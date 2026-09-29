"""App launcher (rofi replacement): a search bar that grows a result list while typing.

Rows come from services.launcher.Sources: apps, calculator, power, "> cmd", "c:" clipboard, ":" emoji.
"""

from __future__ import annotations

from typing import Any

from fabric.utils.helpers import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay
from gi.repository import Gdk, GLib, Gtk, Pango

from services.launcher import Item, Sources
from services.monitors import Monitor
from shared.widgets import flag, slide, text
from shared.window import PopupWindow


def _web_icon() -> Any:
    try:
        return Gtk.IconTheme.get_default().load_icon("chromium", 28, Gtk.IconLookupFlags.FORCE_SIZE)
    except GLib.Error:
        return "\U000F059F"  # nf-md-web


class LauncherWindow(PopupWindow):
    def __init__(self, monitor: Monitor):
        self.sources = Sources()
        self.items: list[Item] = []
        self.labels: list[Label] = []
        self.selected = 0
        self.armed: int | None = None  # confirm row waiting for a second Enter
        self.web_icon = _web_icon()
        self.entry = Entry(h_expand=True, style_classes=("launcher-entry",))
        # GTK3 hides an Entry's own placeholder while it has focus, and ours always does
        self.placeholder = text("Search Applications...", "launcher-placeholder", xalign=0)
        self.entry.connect("changed", lambda *_: self.refresh())
        self.entry.connect("activate", lambda *_: self.launch(self.selected))
        self.list = Box(orientation="v", spacing=2, style_classes=("launcher-list",))
        self.reveal = slide(self.list, "down")
        panel = Box(
            orientation="v",
            style_classes=("launcher",),
            children=[
                Box(spacing=10, style_classes=("launcher-search",), children=[text("\U000F0349", "launcher-icon"), Overlay(child=self.entry, overlays=[self.placeholder], h_expand=True)]),
                self.reveal,
            ],
        )
        super().__init__(
            monitor,
            title="fabric-launcher",
            dismissible=True,
            hotkey="d",
            geometry="top",
            margin=f"{monitor.height // 4}px 0px 0px 0px",
            size=(520, -1),
            child=panel,
        )
        self.clip_to(12, panel)
        self.connect("key-press-event", self._on_nav)
        self.connect("show", lambda *_: self._open())
        self.connect("map-event", lambda *_: self.take_focus())

    def _open(self) -> None:
        # ponytail: rescans .desktop files on every open (~ms); cache + Gio.AppInfoMonitor if it ever lags
        self.sources.reset(get_desktop_applications())
        self.entry.set_text("")
        self.entry.grab_focus()
        self.refresh()

    def refresh(self) -> None:
        query = self.entry.get_text()
        self.placeholder.set_visible(not query)
        self.items = self.sources.search(query, lambda app: app.get_icon_pixbuf(28, "application-x-executable"), self.web_icon)
        self.selected, self.armed, self.labels = 0, None, []
        for child in self.list.get_children():
            child.destroy()
        for index, item in enumerate(self.items):
            icon = text(item.icon, "launcher-glyph") if isinstance(item.icon, str) else Image(pixbuf=item.icon)
            name = text(item.label, "launcher-name", xalign=0)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            # caps the natural width so long clipboard entries ellipsize instead of widening the window
            name.set_max_width_chars(1)
            name.set_hexpand(True)
            self.labels.append(name)
            body: Any = name
            if item.detail:
                detail = text(item.detail, "launcher-detail", xalign=0)
                detail.set_ellipsize(Pango.EllipsizeMode.END)
                body = Box(orientation="v", h_expand=True, children=[name, detail])
            self.list.add(Button(
                style_classes=("launcher-row",),
                child=Box(spacing=10, children=[icon, body]),
                on_clicked=lambda *_, i=index: self.launch(i),
            ))
        self.list.show_all()
        self._highlight()
        self.reveal.set_reveal_child(bool(self.items))

    def _highlight(self) -> None:
        for index, row in enumerate(self.list.get_children()):
            flag(row, "selected", index == self.selected)

    def _on_nav(self, _widget: Any, event: Gdk.EventKey) -> bool:
        step = {Gdk.KEY_Down: 1, Gdk.KEY_Tab: 1, Gdk.KEY_Up: -1, Gdk.KEY_ISO_Left_Tab: -1}.get(event.keyval)
        if step is None or not self.items:
            return False
        self._disarm()
        self.selected = (self.selected + step) % len(self.items)
        self._highlight()
        return True

    def _disarm(self) -> None:
        if self.armed is not None:
            self.labels[self.armed].set_text(self.items[self.armed].label)
            flag(self.list.get_children()[self.armed], "armed", False)
            self.armed = None

    def launch(self, index: int) -> None:
        if index >= len(self.items):
            return
        item = self.items[index]
        if item.confirm and self.armed != index:
            self._disarm()
            self.armed = index
            self.labels[index].set_text(f"{item.label}: press Enter again to confirm")
            flag(self.list.get_children()[index], "armed", True)
            return
        self.hide()
        item.action()


def build(context: Any) -> list[Any]:
    context.launcher = LauncherWindow(context.monitors[0])
    return [context.launcher]
