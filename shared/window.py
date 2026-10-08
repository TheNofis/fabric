"""Window base classes: every shell window extends one of these."""

from __future__ import annotations

import ctypes
import ctypes.util
import math
from collections.abc import Callable
from typing import Any

import cairo
from fabric.widgets.x11 import X11Window
from gi.repository import Gdk, GLib, Gtk

from services.monitors import Monitor
from shared.constants import CONTENT_GAP, POPUP_TOP

_xlib = ctypes.CDLL(ctypes.util.find_library("X11"))
_xext = ctypes.CDLL(ctypes.util.find_library("Xext"))
_xlib.XOpenDisplay.restype = ctypes.c_void_p
_xlib.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.POINTER(ctypes.c_ulong)] * 2 + [ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
_xlib.XFree.argtypes = [ctypes.c_void_p]
_xlib.XFlush.argtypes = [ctypes.c_void_p]
_xlib.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
_xext.XShapeCombineShape.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
_display = _xlib.XOpenDisplay(None)


def _copy_shape_to_frame(xid: int) -> None:
    # i3 reparents managed windows (docks, notifications) into an unshaped frame and picom
    # blurs the frame, so it needs the client's shape. Override-redirect popups have no frame.
    root, parent, children, count = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
    if not _xlib.XQueryTree(_display, xid, ctypes.byref(root), ctypes.byref(parent), ctypes.byref(children), ctypes.byref(count)):
        return
    if children:
        _xlib.XFree(children)
    if parent.value and parent.value != root.value:
        _xext.XShapeCombineShape(_display, parent.value, 0, 0, 0, xid, 0, 0)  # ShapeBounding, ShapeSet
        _xlib.XFlush(_display)


class MonitorWindow(X11Window):
    def __init__(self, monitor: Monitor, **kwargs: Any):
        self.monitor = monitor
        super().__init__(**kwargs)

    def clip_to(self, radius: int, *watch: Gtk.Widget, parts: Callable[[], list[Gtk.Widget]] | None = None) -> None:
        """Shape the window to rounded panels so picom blurs only behind them.

        radius must match the panels' CSS border-radius. parts lists the panels when
        they change at runtime (e.g. notification cards); watch widgets trigger a reshape.
        """

        shaped: list[tuple[int, int, int, int]] = []

        def reshape(*_: Any, force: bool = False) -> None:
            window = self.get_window()
            if window is None:
                return
            rects = []
            for panel in parts() if parts else watch:
                if not panel.get_visible():
                    continue
                x, y = panel.translate_coordinates(self, 0, 0)
                width, height = panel.get_allocated_width(), panel.get_allocated_height()
                if width >= 2 * radius and height >= 2 * radius:  # else not laid out yet
                    rects.append((x, y, width, height))
            # size-allocate fires on every relayout (a row's font-weight on each arrow key); an
            # unchanged shape still makes picom redo the blur, which stutters held-key navigation
            if not rects or (rects == shaped and not force):
                return  # an empty shape would make the mapped window invisible; the next allocation reshapes
            shaped[:] = rects
            region = cairo.Region()
            for x, y, width, height in rects:
                for row in range(height):
                    edge = min(row, height - 1 - row)
                    inset = round(radius - math.sqrt(radius**2 - (radius - edge - 0.5) ** 2)) if edge < radius else 0
                    region.union(cairo.RectangleInt(x + inset, y + row, width - 2 * inset, 1))
            window.shape_combine_region(region, 0, 0)
            window.get_display().flush()
            # picom drops damage that races a shape change and keeps stale pixels; repaint once it caught up
            GLib.timeout_add(50, lambda: self.queue_draw() or False)
            _copy_shape_to_frame(window.get_xid())

        for widget in watch:
            widget.connect("size-allocate", reshape)
        # i3 reparents on map, so the new frame needs the shape too; idle so the first map sees the allocation
        self.connect("map-event", lambda *_: GLib.idle_add(lambda: reshape(force=True) or False))

    def take_focus(self) -> None:
        """Give the window X input focus: override-redirect popups never get it from i3,
        and without it GTK entries ignore typed keys."""
        window = self.get_window()
        if window is not None:
            _xlib.XSetInputFocus(_display, window.get_xid(), 1, 0)  # RevertToPointerRoot, CurrentTime
            _xlib.XFlush(_display)

    def do_get_display_props(self):
        display = Gdk.Display.get_default()
        rectangle = Gdk.Rectangle()
        rectangle.x, rectangle.y = self.monitor.x, self.monitor.y
        rectangle.width, rectangle.height = self.monitor.width, self.monitor.height
        return display, rectangle, 1


class OverlayWindow(MonitorWindow):
    """Transient window that shows without taking focus (OSD, notification popups).

    Override-redirect, so i3 doesn't manage it: a managed window gets focused on map,
    which moves focus to its monitor and warps the pointer there.
    """

    def __init__(self, monitor: Monitor, **kwargs: Any):
        super().__init__(monitor, **{"type": "popup", "type_hint": "notification", "focusable": False, "visible": False, **kwargs})


class PopupWindow(MonitorWindow):
    """Hidden-by-default dialog that opens/closes with toggle().

    Children whose visibility is driven by state must set_no_show_all(True),
    otherwise show_all() on open reveals them.

    dismissible grabs pointer and keyboard while shown: Esc or a click outside
    the popup (and outside other shell windows) hides it; the click is swallowed.
    While open, i3 keybindings don't fire (the keyboard is grabbed), so hotkey
    names the key of the i3 Super+<key> binding that opens it; it closes the popup too.

    Dismissible popups stack: one opening over another (a polkit prompt over the network
    panel) takes the grab and the one below stays open; closing the top hands the grab back.
    """

    grabbed: list[PopupWindow] = []  # popups holding the grab, the current holder last
    # False (password prompts): a click outside is swallowed and a lost grab is re-taken, only Esc/hotkey close
    outside_click_closes = True

    def __init__(self, monitor: Monitor, dismissible: bool = False, hotkey: str | None = None, **kwargs: Any):
        super().__init__(monitor, **{"type": "popup", "type_hint": "dialog", "visible": False, **kwargs})
        self.hotkey = Gdk.keyval_from_name(hotkey) if hotkey else None
        if dismissible:
            self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK)
            self.connect("map-event", lambda *_: self._grab())
            self.connect("unmap-event", lambda *_: self._release())
            self.connect("button-press-event", self._on_button_press)
            self.connect("key-press-event", self._on_key_press)
            # grab_window is set when one of our own windows took the grab: stay open below it
            self.connect("grab-broken-event", self._on_grab_broken)

    def _seat(self) -> Gdk.Seat:
        return self.get_display().get_default_seat()

    def _grab(self, attempts: int = 50) -> bool:
        # owner_events=True: events for our own windows are delivered normally,
        # everything else is reported to this window.
        # Fails with ALREADY_GRABBED while i3 still holds the opening hotkey or the bar
        # holds the implicit grab of the opening click, so retry until it's released.
        if not self.get_visible() or self.get_window() is None:
            return False
        status = self._seat().grab(self.get_window(), Gdk.SeatCapabilities.ALL, True, None, None, None, None)
        if status == Gdk.GrabStatus.SUCCESS:
            if self in PopupWindow.grabbed:
                PopupWindow.grabbed.remove(self)
            PopupWindow.grabbed.append(self)
        elif attempts > 0:
            GLib.timeout_add(20, self._grab, attempts - 1)
        return False

    def _release(self) -> None:
        # ungrab only our own grab: after a popup above took it, ungrabbing would strip that
        # popup's keyboard (its entry stops taking input)
        if self not in PopupWindow.grabbed:
            return
        holder = PopupWindow.grabbed[-1] is self
        PopupWindow.grabbed.remove(self)
        if holder:
            self._seat().ungrab()
            if PopupWindow.grabbed:
                below = PopupWindow.grabbed[-1]
                below._grab()
                below.take_focus()

    def _on_grab_broken(self, _widget: Any, event: Gdk.EventGrabBroken) -> bool:
        if event.grab_window is None:
            self.hide() if self.outside_click_closes else GLib.timeout_add(100, self._grab)
        return False

    def _on_button_press(self, _widget: Any, event: Gdk.EventButton) -> bool:
        x, y = self.get_window().get_origin()[1:]
        width, height = self.get_size()
        if not (x <= event.x_root < x + width and y <= event.y_root < y + height):
            if self.outside_click_closes:
                self.hide()
            return True
        return False

    def _on_key_press(self, _widget: Any, event: Gdk.EventKey) -> bool:
        # keyval as on the first layout, like i3's bindsym, so Super+ь still matches m
        base = Gdk.Keymap.get_for_display(self.get_display()).translate_keyboard_state(event.hardware_keycode, 0, 0)[1]
        is_hotkey = base == self.hotkey and event.state & (Gdk.ModifierType.MOD4_MASK | Gdk.ModifierType.SUPER_MASK)
        if event.keyval == Gdk.KEY_Escape or is_hotkey:
            self.hide()
            return True
        return False

    def toggle(self) -> None:
        self.hide() if self.get_visible() else self.show_all()


class FocusPopup(PopupWindow):
    """Dismissible popup centred near the top that takes the keyboard (launcher, Dayline, Messages):
    monitor.height // drop below the top edge, shaped to `child` with the panel radius."""

    def __init__(self, monitor: Monitor, title: str, hotkey: str, child: Gtk.Widget, drop: int, width: int = -1, radius: int = 20):
        super().__init__(monitor, title=title, dismissible=True, hotkey=hotkey, geometry="top",
                         margin=f"{monitor.height // drop}px 0px 0px 0px", size=(width, -1), child=child)
        self.clip_to(radius, child)
        self.connect("map-event", lambda *_: self.take_focus())


class BarPanel(PopupWindow):
    """Dropdown under a bar slot (calendar, system monitor), like a macOS menu extra.

    toggle_at(slot) aligns the panel's left edge with the slot, kept inside the tiled-window
    area. One panel is open at a time: opening one closes the rest. Esc or a click outside
    closes it; clicks on the bar still reach it, so the slot toggles and a sibling slot switches.
    """

    instances: list[BarPanel] = []

    def __init__(self, monitor: Monitor, name: str, child: Gtk.Widget, radius: int = 18):
        super().__init__(monitor, dismissible=True, title=f"fabric-{name}-{monitor.name}", geometry="top-left", child=child)
        BarPanel.instances.append(self)
        self.clip_to(radius, child)

    def toggle_at(self, slot: Gtk.Widget) -> None:
        if self.get_visible():
            self.hide()
            return
        for panel in BarPanel.instances:
            if panel is not self:
                panel.hide()  # first, so a panel sharing a stream stops it before this one starts it
        x = slot.translate_coordinates(slot.get_toplevel(), 0, 0)[0]
        width = self.get_child().get_preferred_width()[1]
        # 16px = .popup side padding, so the panel's content lines up with the slot's
        x = min(max(x - 16, CONTENT_GAP), self.monitor.width - width - CONTENT_GAP)
        self.margin = (POPUP_TOP, x, 0, 0)  # Fabric: x offset = right - left margin
        self.show_all()
