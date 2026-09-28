"""Notification popups and notification center (Fabric D-Bus service)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from fabric.notifications import Notifications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import GLib, Gtk, Pango

from services.monitors import Monitor
from services.system import ClockState
from shared.constants import CONTENT_GAP, DND_FILE, POPUP_TOP
from shared.widgets import css, flag, slide, text
from shared.window import OverlayWindow, PopupWindow


@dataclass
class NotificationRecord:
    id: int
    app: str
    title: str
    body: str
    urgency: int
    action: str
    time: str


class NotificationCard(EventBox):
    def __init__(
        self,
        record: NotificationRecord,
        center: bool,
        activate: Callable[[NotificationRecord], None],
        close: Callable[[NotificationRecord], None],
    ):
        self.progress = progress = Gtk.ProgressBar()
        css(progress, "notification-progress")
        progress.set_fraction(1)
        if center:
            progress.hide()
            progress.set_no_show_all(True)
        title = text(record.title, "notification-title", xalign=0)
        body = text("", "notification-body", xalign=0)
        # we advertise body-markup: render it, but fall back to plain text on broken markup
        try:
            Pango.parse_markup(record.body, -1, "\0")
            body.set_markup(record.body)
        except GLib.Error:
            body.set_text(record.body)
        title.set_line_wrap(True)
        title.set_max_width_chars(32)
        body.set_line_wrap(True)
        body.set_max_width_chars(45)
        card = Box(
            orientation="v",
            spacing=4,
            style_classes=("notification", *(('critical',) if record.urgency == 2 else ())),
            children=[
                Box(
                    spacing=10,
                    children=[
                        text("󰂚", "notification-icon"),
                        Box(
                            orientation="v",
                            spacing=1,
                            h_expand=True,
                            children=[
                                text(record.app, "notification-app", xalign=0),
                                title,
                            ],
                        ),
                        text(record.time, "notification-time"),
                        Button(label="×", style_classes=("notification-close",), on_clicked=lambda *_: close(record)),
                    ],
                ),
                body,
                progress,
            ],
        )
        super().__init__(
            events="button-press",
            child=card,
            on_button_press_event=lambda *_: activate(record) or True,
        )


class NotificationHub:
    def __init__(self, monitor: Monitor, clock: ClockState):
        self.records: list[NotificationRecord] = []
        self.history_widgets: dict[int, NotificationCard] = {}
        self.popup_widgets: dict[int, NotificationCard] = {}
        self.timers: dict[int, int] = {}
        self.deadlines: dict[int, tuple[float, float]] = {}  # id -> (monotonic end, timeout s)
        self.progress_timer = 0
        self.dnd = DND_FILE.exists()
        self.history = Box(orientation="v", spacing=10)
        self.popups = Box(orientation="v", spacing=8, style_classes=("notification-stack",))
        self.dnd_label = text("󰂛" if self.dnd else "󰂚")
        self.dnd_button = Button(
            child=self.dnd_label,
            style_classes=("notification-action", *(('active',) if self.dnd else ())),
            on_clicked=lambda *_: self.toggle_dnd(),
        )
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
                        Button(label="󰃢", style_classes=("notification-action",), on_clicked=lambda *_: self.clear()),
                    ],
                ),
                ScrolledWindow(
                    h_scrollbar_policy="never",
                    v_scrollbar_policy="automatic",
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
        self.popup_window.clip_to(14, self.popups, parts=lambda: self.popups.children)
        self.center_window.clip_to(14, center_body)
        self.service = Notifications(on_notification_added=self.add)
        # closed by the app (CloseNotification) or by us: drop the popup, keep history
        self.service.connect("notification-removed", lambda _service, nid: self.remove_popup(nid))

    def add(self, service: Notifications, notification_id: int) -> None:
        notification = service.get_notification_from_id(notification_id)
        if not notification:
            return
        # Fabric always allocates a new id, so honour replaces_id ourselves
        # (volume/progress notifications would otherwise pile up).
        if notification.replaces_id:
            service.remove_notification(notification.replaces_id)
            self.remove_record(notification.replaces_id)
        action = notification.actions[0].identifier if notification.actions else ""
        record = NotificationRecord(
            notification.id,
            notification.app_name,
            notification.summary,
            notification.body,
            notification.urgency,
            action,
            datetime.fromtimestamp(notification.time).strftime("%H:%M"),
        )
        self.records.insert(0, record)
        for old in self.records[50:]:
            self.remove_record(old.id)
        card = NotificationCard(record, True, self.activate, self.close)
        self.history.pack_start(card, False, False, 0)
        self.history.reorder_child(card, 0)
        self.history_widgets[record.id] = card
        card.show_all()
        timeout = notification.timeout if notification.timeout > 0 else 8000
        if not self.dnd:
            popup = NotificationCard(record, False, self.activate, self.close)
            revealer = slide(popup, "down")
            self.popups.pack_start(revealer, False, False, 0)
            self.popup_widgets[record.id] = popup
            revealer.show_all()
            revealer.reveal()  # animates only if the window is already mapped
            self.popup_window.show_all()  # a handful of popups, cheap
            self.deadlines[record.id] = (time.monotonic() + timeout / 1000, timeout / 1000)
            if not self.progress_timer:
                self.progress_timer = GLib.timeout_add(100, self.tick_progress)
        self.timers[record.id] = GLib.timeout_add(timeout, self.expire, record.id)

    def tick_progress(self) -> bool:
        # one shared timer for all popups; stops itself when the stack is empty
        if not self.deadlines:
            self.progress_timer = 0
            return False
        now = time.monotonic()
        for notification_id, (end, total) in self.deadlines.items():
            if widget := self.popup_widgets.get(notification_id):
                widget.progress.set_fraction(max(end - now, 0) / total)
        return True

    def expire(self, notification_id: int) -> bool:
        self.timers.pop(notification_id, None)
        self.remove_popup(notification_id)
        if notification := self.service.notifications.get(notification_id):
            notification.close("expired")
        return False

    def remove_popup(self, notification_id: int) -> None:
        self.deadlines.pop(notification_id, None)
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
        self.remove_popup(notification_id)

    def activate(self, record: NotificationRecord) -> None:
        if record.action:
            self.service.invoke_notification_action(record.id, record.action)
        self.remove_popup(record.id)
        if record.id in self.service.notifications:
            self.service.notifications[record.id].close("dismissed-by-user")

    def close(self, record: NotificationRecord) -> None:
        if record.id in self.service.notifications:
            self.service.notifications[record.id].close("dismissed-by-user")
        self.remove_record(record.id)

    def clear(self) -> None:
        for notification in list(self.service.notifications.values()):
            notification.close("dismissed-by-user")
        for record in list(self.records):
            self.remove_record(record.id)

    def toggle_dnd(self) -> None:
        self.dnd = not self.dnd
        DND_FILE.touch() if self.dnd else DND_FILE.unlink(missing_ok=True)
        self.dnd_label.set_text("󰂛" if self.dnd else "󰂚")
        flag(self.dnd_button, "active", self.dnd)

    def toggle_center(self) -> None:
        self.center_window.toggle()


def build(context: Any) -> list[Any]:
    context.notifications = NotificationHub(context.monitors[0], context.clock)
    return [context.notifications.popup_window, context.notifications.center_window]
