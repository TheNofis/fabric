"""The notification stack and the notification center (Fabric's D-Bus service behind them)."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Callable
from types import SimpleNamespace

from fabric.notifications import Notifications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.revealer import Revealer
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Gdk, GLib, Gtk

from modules.notifications import record as history
from modules.notifications.card import NotificationCard
from modules.notifications.record import NotificationRecord, from_notification
from services.monitors import Monitor
from services import mock
from services.system import ClockState
from services.tether import code_in
from shared.constants import CONTENT_GAP, DND_FILE, POPUP_TOP
from shared.ui import Confirm
from shared.widgets import copy_text, flag, slide, text
from shared.window import OverlayWindow, PopupWindow

MAX_POPUPS = 4


class NotificationHub:
    def __init__(self, monitor: Monitor, clock: ClockState, locked: Callable[[], bool]):
        self.locked = locked
        self.reply: Callable[[NotificationRecord], bool] | None = None  # set by Messages: tether's Reply opens it
        self.records: list[NotificationRecord] = []
        self.history_widgets: dict[int, NotificationCard] = {}
        self.popup_widgets: dict[int, NotificationCard] = {}
        self.timers: dict[int, int] = {}
        self.deadlines: dict[int, tuple[float, float]] = {}  # id -> (monotonic end, timeout s)
        self.paused: dict[int, tuple[float, float]] = {}  # hovered: id -> (remaining s, timeout s)
        self.progress_timer = 0
        self.save_timer = 0
        self.dnd = False if mock.ENABLED else DND_FILE.exists()
        self.history = Box(orientation="v", spacing=10)
        self.empty = text("No notifications", "notification-empty")
        self.empty.set_no_show_all(True)
        self.empty.show()
        self.history.pack_end(self.empty, True, True, 0)
        self.popups = Box(orientation="v", spacing=8, style_classes=("notification-stack",))
        self.dnd_label = text("󰂛" if self.dnd else "󰂚")
        self.dnd_button = Button(
            child=self.dnd_label,
            style_classes=("notification-action", *(('active',) if self.dnd else ())),
            on_clicked=lambda *_: self.toggle_dnd(),
        )
        self.dnd_button.set_tooltip_text("Do not disturb: on" if self.dnd else "Do not disturb: off")
        # clearing is unrecoverable: the first click grows the icon into a red "Clear N?" pill, the second clears
        self.clear_label = text("", "notification-clear-label")
        self.clear_reveal = Revealer(child=self.clear_label, transition_type="slide-left", transition_duration=160)
        self.clear_button = Button(
            child=Box(h_align="center", children=[text("󰃢"), self.clear_reveal]),  # centered: the folded label is 0px wide
            tooltip_text="Clear all",
            style_classes=("notification-action", "notification-clear"),
        )
        self.clear_confirm = Confirm(self.clear_button, self.clear, self.on_arm_clear)
        self.clear_button.set_sensitive(False)
        for button in (self.dnd_button, self.clear_button):
            button.set_can_focus(False)  # pointer-only; otherwise opening the center rings the first one
            button.set_valign(Gtk.Align.CENTER)  # 36px pills, not stretched to the clock's height
        time_label = text("", "notification-center-time", xalign=0)
        date_label = text("", "notification-center-date", xalign=0)
        clock.subscribe(lambda now: (time_label.set_text(now.strftime("%H:%M")), date_label.set_text(now.strftime("%a %d %b"))))
        center_body = Box(
            orientation="v",
            spacing=8,
            style_classes=("notification-center",),
            children=[
                Box(
                    spacing=10,
                    style_classes=("notification-center-head",),
                    children=[
                        Box(orientation="v", h_expand=True, children=[time_label, date_label]),
                        self.dnd_button,
                        self.clear_button,
                    ],
                ),
                ScrolledWindow(
                    h_scrollbar_policy="never",
                    v_scrollbar_policy="automatic",
                    propagate_height=False,  # Fabric defaults to True: the list would grow the window past the screen
                    child=self.history,
                    h_expand=True,
                    v_expand=True,
                ),
            ],
        )
        self.popup_window = OverlayWindow(
            monitor,
            title="fabric-notification-popups",
            geometry="top-right",
            margin=f"{POPUP_TOP}px {-CONTENT_GAP}px 0px 0px",
            size=(380, -1),
            child=self.popups,
        )
        self.center_window = PopupWindow(
            monitor,
            title="fabric-notification-center",
            dismissible=True,
            hotkey="n",
            geometry="top-right",
            margin=f"{POPUP_TOP}px {-CONTENT_GAP}px 0px 0px",
            size=(400, monitor.height - POPUP_TOP - CONTENT_GAP),
            child=center_body,
        )
        self.popup_window.clip_to(18, self.popups, parts=lambda: self.popups.children)
        self.center_window.clip_to(24, center_body)
        self.center_window.connect("hide", lambda *_: self.clear_confirm.arm(False))
        self.service = SimpleNamespace(notifications={}) if mock.ENABLED else Notifications(on_notification_added=self.add)
        # closed by the app (CloseNotification) or by us: drop the popup, keep history
        if not mock.ENABLED:
            self.service.connect("notification-removed", lambda _service, nid: self.remove_popup(nid))
            self.restore()

    def card(self, record: NotificationRecord, center: bool) -> NotificationCard:
        card = NotificationCard(record, center, self.activate, self.close)
        # a link opens the browser: drop the center's grab so it takes focus
        card.body.connect("activate-link", lambda *_: self.center_window.hide() or False)
        return card

    def add_history(self, record: NotificationRecord) -> None:
        self.records.insert(0, record)
        card = self.card(record, True)
        self.history.pack_start(card, False, False, 0)
        self.history.reorder_child(card, 0)
        self.history_widgets[record.id] = card
        card.show_all()
        self.empty.set_visible(False)
        self.clear_button.set_sensitive(True)

    def save(self) -> None:
        # coalesce bursts (clear all, a spammy sender) into one write
        if not mock.ENABLED and not self.save_timer:
            self.save_timer = GLib.timeout_add(500, self.flush)

    def flush(self) -> bool:
        self.save_timer = 0
        history.save(self.records)
        return False

    def restore(self) -> None:
        for record in history.load():
            self.add_history(record)

    def add(self, service: Notifications, notification_id: int) -> None:
        notification = service.get_notification_from_id(notification_id)
        if not notification:
            return
        # Fabric always allocates a new id, so honour replaces_id ourselves
        # (volume/progress notifications would otherwise pile up).
        if notification.replaces_id:
            service.remove_notification(notification.replaces_id)
            self.remove_record(notification.replaces_id)
        record = from_notification(notification)
        self.add_history(record)
        for old in self.records[50:]:
            self.close(old)
        self.save()
        timeout = notification.timeout if notification.timeout > 0 else 8000
        # locked: the lock re-raises once a second, a popup would flash over it;
        # do-not-disturb holds back everything but critical (low battery and the like)
        if (not self.dnd or record.urgency == 2) and not self.locked():
            while len(self.popup_widgets) >= MAX_POPUPS:  # a burst: the oldest yields, it stays in the center
                self.remove_popup(next(iter(self.popup_widgets)))
            popup = self.card(record, False)
            popup.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
            popup.connect("enter-notify-event", lambda _w, event: self.hover(record.id, True, event))
            popup.connect("leave-notify-event", lambda _w, event: self.hover(record.id, False, event))
            revealer = slide(popup, "down")
            self.popups.pack_start(revealer, False, False, 0)
            self.popup_widgets[record.id] = popup
            revealer.show_all()
            revealer.reveal()  # animates only if the window is already mapped
            self.popup_window.show_all()  # a handful of popups, cheap
            if not record.persistent:
                self.arm(record.id, timeout / 1000, timeout / 1000)
        elif not record.persistent:
            self.timers[record.id] = GLib.timeout_add(timeout, self.expire, record.id)

    def arm(self, notification_id: int, remaining: float, total: float) -> None:
        self.deadlines[notification_id] = (time.monotonic() + remaining, total)
        self.timers[notification_id] = GLib.timeout_add(int(remaining * 1000), self.expire, notification_id)
        if not self.progress_timer:
            # frame clock, not a fixed timer: the bar moves once per vsync instead of stepping
            self.progress_timer = self.popup_window.add_tick_callback(lambda *_: self.tick_progress())

    def hover(self, notification_id: int, inside: bool, event: Gdk.EventCrossing) -> bool:
        if event.detail == Gdk.NotifyType.INFERIOR:  # pointer moved onto the close button, still inside
            return False
        if inside and (timer := self.timers.pop(notification_id, None)):
            GLib.source_remove(timer)
            end, total = self.deadlines.pop(notification_id)
            self.paused[notification_id] = (end - time.monotonic(), total)
        elif not inside and (paused := self.paused.pop(notification_id, None)):
            self.arm(notification_id, max(paused[0], 1.5), paused[1])  # never vanish the instant the pointer leaves
        return False

    def tick_progress(self) -> bool:
        # one shared frame callback for all popups; stops itself when the stack is empty
        if not self.deadlines:
            self.progress_timer = 0
            return False
        now = time.monotonic()
        for notification_id, (end, total) in self.deadlines.items():
            if widget := self.popup_widgets.get(notification_id):
                widget.progress.set_fraction(max(end - now, 0) / total)
        return True

    def expire(self, notification_id: int) -> bool:
        # only the popup expires: closing on the bus makes the sender drop its actions,
        # and clicking the card in the center would then do nothing
        self.timers.pop(notification_id, None)
        self.remove_popup(notification_id)
        return False

    def remove_popup(self, notification_id: int) -> None:
        self.deadlines.pop(notification_id, None)
        self.paused.pop(notification_id, None)
        if timer := self.timers.pop(notification_id, None):
            GLib.source_remove(timer)
        if widget := self.popup_widgets.pop(notification_id, None):
            revealer = widget.get_parent()
            if self.popup_widgets:
                revealer.unreveal()  # slide this card out, the rest of the stack stays
                GLib.timeout_add(revealer.get_transition_duration(), lambda: revealer.destroy() or False)
            else:
                # unmap before emptying the window so picom animates the last card, not a blank
                self.popup_window.hide()
                revealer.destroy()

    def remove_record(self, notification_id: int) -> None:
        self.records = [record for record in self.records if record.id != notification_id]
        if widget := self.history_widgets.pop(notification_id, None):
            self.history.remove(widget)
            widget.destroy()
        self.empty.set_visible(not self.records)
        self.clear_button.set_sensitive(bool(self.records))
        self.remove_popup(notification_id)
        self.save()

    def on_arm_clear(self, on: bool) -> None:
        # Confirm disarms after 3s or when the pointer leaves the button; closing the center does too
        if on:
            self.clear_label.set_text(f"Clear {len(self.records)}?")
        self.clear_reveal.set_reveal_child(on)
        self.clear_button.set_tooltip_text(None if on else "Clear all")

    def activate(self, record: NotificationRecord, action: str) -> None:
        code = code_in(f"{record.title} {record.body}") if action == "copy-code" else None
        if code:  # tether's own copy needs a Wayland clipboard; this is X11
            copy_text(code)
            self.close(record)
            return
        if action == "reply" and self.reply and record.app.lower() in ("tether", "iphone") and self.reply(record):
            self.center_window.hide()
            self.close(record)  # answered in the Messages panel instead of tether's own window
            return
        if action:
            self.service.invoke_notification_action(record.id, action)
            self.center_window.hide()  # drop the grab so the opened window takes input
            self.close(record)  # one-shot: the sender drops the notification after its action
            return
        self.remove_popup(record.id)
        if record.id in self.service.notifications:
            self.service.notifications[record.id].close("dismissed-by-user")

    def close(self, record: NotificationRecord) -> None:
        if record.id in self.service.notifications:
            self.service.notifications[record.id].close("dismissed-by-user")
        self.remove_record(record.id)

    def clear(self) -> None:
        self.clear_confirm.arm(False)
        for notification in list(self.service.notifications.values()):
            notification.close("dismissed-by-user")
        for record in list(self.records):
            self.remove_record(record.id)

    def seed_mock(self) -> None:
        """Populate history without a D-Bus notification producer."""
        today = datetime.now().replace(second=0, microsecond=0)
        for record in (
            NotificationRecord(9001, "Fabric", "Voice input ready", "Speak a phrase and it will be typed into the focused field.", 0, "", today.replace(hour=10, minute=29).timestamp()),
            NotificationRecord(9002, "Dayline", "Reminder in 30 minutes", "Ship the new shell panels", 0, "", today.replace(hour=10, minute=0).timestamp()),
            NotificationRecord(9003, "Claude", "Usage is on pace", "Session 63% · resets today 12:48", 0, "", today.replace(hour=9, minute=45).timestamp()),
        ):
            self.add_history(record)
            if record.id != 9003:
                popup = self.card(record, False)
                self.popups.add(slide(popup, "down"))
                self.popup_widgets[record.id] = popup
        self.popups.show_all()
        for revealer in self.popups.get_children():
            revealer.reveal()

    def toggle_dnd(self) -> None:
        self.dnd = not self.dnd
        if not mock.ENABLED:
            DND_FILE.touch() if self.dnd else DND_FILE.unlink(missing_ok=True)
        self.dnd_label.set_text("󰂛" if self.dnd else "󰂚")
        flag(self.dnd_button, "active", self.dnd)
        self.dnd_button.set_tooltip_text("Do not disturb: on" if self.dnd else "Do not disturb: off")

    def toggle_center(self) -> None:
        self.center_window.toggle()
