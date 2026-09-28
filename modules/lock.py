"""Lock screen: a blurred cover on every monitor, password entry on the first one (PAM check)."""

from __future__ import annotations

import threading
from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.entry import Entry
from gi.repository import Gdk, GLib

from services.monitors import Monitor
from services.pam import authenticate
from shared.widgets import flag, text
from shared.window import MonitorWindow


class LockWindow(MonitorWindow):
    def __init__(self, monitor: Monitor, context: Any, entry: Entry | None = None):
        time, date = text("", "lock-time"), text("", "lock-date")
        center = Box(orientation="v", spacing=6, h_expand=True, v_expand=True, h_align="center", v_align="center", children=[time, date])
        if entry is not None:
            layout = text("", "lock-layout")
            center.add(Box(spacing=10, style_classes=("lock-input",), children=[text("\U000F033E", "lock-icon"), entry, layout]))  # nf-md-lock
            context.keyboard.subscribe(lambda v: layout.set_text(str(v.get("layout", "us")) + (" 󰪛" if v.get("caps") else "")))
        super().__init__(
            monitor,
            title="fabric-lock",
            type="popup",
            type_hint="dialog",
            geometry="top-left",
            size=(monitor.width, monitor.height),
            visible=False,
            child=Box(style_classes=("lock",), children=[center]),
        )
        context.clock.subscribe(lambda now: (time.set_text(now.strftime("%H:%M")), date.set_text(now.strftime("%A, %d %B"))))


class Lock:
    """All lock windows; the first one owns the entry and the keyboard/pointer grab."""

    def __init__(self, context: Any):
        self.entry = Entry(h_expand=True, style_classes=("lock-entry",))
        self.entry.set_visibility(False)  # Fabric's Entry ignores a visibility kwarg
        self.entry.set_invisible_char("●")
        self.entry.connect("activate", lambda *_: self._check())
        self.windows = [LockWindow(m, context, self.entry if i == 0 else None) for i, m in enumerate(context.monitors)]
        main = self.windows[0]
        main.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
        main.connect("map-event", lambda *_: (main.take_focus(), self.entry.grab_focus(), self._grab()))
        main.connect("grab-broken-event", lambda *_: GLib.timeout_add(100, self._grab) and False)
        self.locked = False

    def lock(self) -> None:
        if self.locked:
            return
        self.locked = True
        self.entry.set_text("")
        self.entry.set_sensitive(True)
        for window in self.windows:
            window.show_all()
        # ponytail: i3 stacks newly mapped managed windows (notifications) above us; re-raise each second
        GLib.timeout_add(1000, self._raise)

    def _raise(self) -> bool:
        for window in self.windows:
            if window.get_window():
                window.get_window().raise_()
        return self.locked

    def _grab(self) -> bool:
        # Retries forever: while locked, keys must never reach other windows.
        # Fails while i3 still holds the hotkey that triggered the lock.
        main = self.windows[0]
        if not self.locked or main.get_window() is None:
            return False
        seat = main.get_display().get_default_seat()
        if seat.grab(main.get_window(), Gdk.SeatCapabilities.ALL, False, None, None, None, None) != Gdk.GrabStatus.SUCCESS:
            GLib.timeout_add(50, self._grab)
        return False

    def _check(self) -> None:
        password = self.entry.get_text()
        if not password or not self.entry.get_sensitive():
            return
        self.entry.set_sensitive(False)
        flag(self.entry.get_parent(), "failed", False)

        def work() -> None:
            ok = authenticate(password)
            GLib.idle_add(self._done, ok)

        threading.Thread(target=work, daemon=True).start()

    def _done(self, ok: bool) -> bool:
        self.entry.set_sensitive(True)
        self.entry.set_text("")
        if ok:
            self.locked = False
            self.windows[0].get_display().get_default_seat().ungrab()
            for window in self.windows:
                window.hide()
        else:
            flag(self.entry.get_parent(), "failed", True)
            self.entry.grab_focus()
        return False


def build(context: Any) -> list[Any]:
    context.lock = Lock(context)
    return context.lock.windows
