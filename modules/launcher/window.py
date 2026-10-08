"""App launcher (rofi replacement): a search bar that grows a result list while typing.

Rows come from services.launcher.Sources: apps, calculator, power, "> cmd", "c:" clipboard, ":" emoji.
"""

from __future__ import annotations

from typing import Any

from fabric.utils.helpers import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from gi.repository import Gdk, GLib, Gtk

from services.launcher import Item, Sources
from services import mock
from services.monitors import Monitor
from shared.ui import Glider, HintEntry
from shared.widgets import flag, line, slide, text
from shared.window import FocusPopup


def _web_icon() -> Any:
    try:
        return Gtk.IconTheme.get_default().load_icon("chromium", 28, Gtk.IconLookupFlags.FORCE_SIZE)
    except GLib.Error:
        return "\U000F059F"  # nf-md-web


class LauncherWindow(FocusPopup):
    def __init__(self, monitor: Monitor):
        self.sources = Sources()
        self.items: list[Item] = []
        self.labels: list[Label] = []
        self.selected = 0
        self.armed: int | None = None  # confirm row waiting for a second Enter
        self.web_icon = _web_icon()
        search = HintEntry("Search apps, sites, or calculate…", "launcher-entry", hint_class="launcher-placeholder")
        self.entry = search.entry
        # the prefixes the placeholder has no room for; shown only while the query is empty
        self.hint = text("> command     c: clipboard     :emoji     ssh", "launcher-hint", xalign=0)
        self.entry.connect("changed", lambda *_: self.refresh())
        self.entry.connect("activate", lambda *_: self.launch(self.selected))
        self.rows = Box(orientation="v", spacing=2, h_expand=True)  # bare: the selection thumb is painted under the rows
        self.list = Box(style_classes=("launcher-list",), children=[self.rows])
        self.glider = Glider(self.rows, "launcher-thumb")
        self.reveal = slide(self.list, "down")
        panel = Box(
            orientation="v",
            style_classes=("launcher",),
            children=[
                Box(spacing=10, style_classes=("launcher-search",), children=[text("\U000F0349", "launcher-icon"), search]),
                self.hint,
                self.reveal,
            ],
        )
        super().__init__(monitor, "fabric-launcher", "d", panel, drop=4, width=520)
        self.connect("key-press-event", self._on_nav)
        self.connect("show", lambda *_: self._open())

    def _open(self) -> None:
        # ponytail: rescans .desktop files on every open (~ms); cache + Gio.AppInfoMonitor if it ever lags
        self.sources.reset([] if mock.ENABLED else get_desktop_applications())
        self.entry.set_text("")
        self.entry.grab_focus()
        self.refresh()

    def refresh(self) -> None:
        query = self.entry.get_text()
        self.hint.set_visible(not query)
        self.items = self.sources.search(query, lambda app: app.get_icon_pixbuf(28, "application-x-executable"), self.web_icon)
        self.selected, self.armed, self.labels = 0, None, []
        for child in self.rows.get_children():
            child.destroy()
        for index, item in enumerate(self.items):
            icon = text(item.icon, "launcher-glyph") if isinstance(item.icon, str) else Image(pixbuf=item.icon)
            name = line(item.label, "launcher-name")  # long clipboard entries ellipsize instead of widening the window
            self.labels.append(name)
            body: Any = name
            if item.detail:
                body = Box(orientation="v", h_expand=True, children=[name, line(item.detail, "launcher-detail")])
            row = Button(
                style_classes=("launcher-row",),
                child=Box(spacing=10, children=[icon, body]),
                on_clicked=lambda *_, i=index: self.launch(i),
            )
            # motion, not enter: a re-render under a resting pointer sends enter but no motion
            row.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
            row.connect("motion-notify-event", lambda *_, i=index: self._hover(i))
            self.rows.add(row)
        self.list.show_all()
        self._highlight(animate=False)  # a new result list: the thumb lands on the first row
        self.reveal.set_reveal_child(bool(self.items))

    def _highlight(self, animate: bool = True) -> None:
        for index, row in enumerate(self.rows.get_children()):
            flag(row, "selected", index == self.selected)
        self.glider.to(self.selected if self.items else None, animate)

    def _hover(self, index: int) -> None:
        if index != self.selected:
            self._disarm()
            self.selected = index
            self._highlight()

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
            flag(self.rows.get_children()[self.armed], "armed", False)
            self.armed = None

    def launch(self, index: int) -> None:
        if index >= len(self.items):
            return
        item = self.items[index]
        if item.confirm and self.armed != index:
            self._disarm()
            self.armed = index
            self.labels[index].set_text(f"{item.label}: press Enter or click again to confirm")
            flag(self.rows.get_children()[index], "armed", True)
            return
        self.hide()
        item.action()
