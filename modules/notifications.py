"""Notification popups and notification center (Fabric D-Bus service)."""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable
from types import SimpleNamespace

from fabric.notifications import Notifications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.scrolledwindow import ScrolledWindow
import cairo
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from services.monitors import Monitor
from services import mock
from services.system import ClockState
from shared.constants import CONTENT_GAP, DND_FILE, POPUP_TOP, RUNTIME
from shared.ui import meter
from shared.widgets import css, flag, slide, text
from shared.window import OverlayWindow, PopupWindow


@dataclass
class NotificationRecord:
    id: int
    app: str
    title: str
    body: str
    urgency: int
    action: str  # the "default" action, run by clicking the card
    time: float  # unix timestamp
    icon: GdkPixbuf.Pixbuf | None = None
    persistent: bool = False  # critical, or the sender asked for timeout 0: never expires
    image: GdkPixbuf.Pixbuf | None = None  # body <img>
    avatar: GdkPixbuf.Pixbuf | None = None  # image-data/image-path: sender avatar, album art
    buttons: list[tuple[str, str]] = field(default_factory=list)  # the other actions: (id, label)
    icon_buttons: bool = False  # action-icons hint: labels are icon names


HISTORY_FILE = RUNTIME / "notifications.json"
PIXBUFS = ("icon", "image", "avatar")
MAX_POPUPS = 4
AVATAR = 40


def png(pixbuf: GdkPixbuf.Pixbuf | None) -> str | None:
    return base64.b64encode(pixbuf.save_to_bufferv("png", [], [])[1]).decode() if pixbuf else None


def unpng(data: str | None) -> GdkPixbuf.Pixbuf | None:
    if not data:
        return None
    loader = GdkPixbuf.PixbufLoader()
    loader.write(base64.b64decode(data))
    loader.close()
    return loader.get_pixbuf()


def stamp(timestamp: float) -> str:
    moment = datetime.fromtimestamp(timestamp)
    if moment.date() == date.today():
        return moment.strftime("%H:%M")
    return "Yesterday" if moment.date() == date.today() - timedelta(days=1) else moment.strftime("%a %d")


def app_icon(notification: Any) -> GdkPixbuf.Pixbuf | None:
    # the app icon as a path or theme name
    try:
        name = notification.app_icon.removeprefix("file://")
        if name.startswith("/"):
            return GdkPixbuf.Pixbuf.new_from_file_at_size(name, 20, 20)
        if name:
            return Gtk.IconTheme.get_default().load_icon(name, 20, Gtk.IconLookupFlags.FORCE_SIZE)
    except GLib.Error:
        pass
    return None


def avatar(notification: Any) -> GdkPixbuf.Pixbuf | None:
    # sender image cropped to a centered square
    try:
        pixbuf = notification.image_pixbuf
    except GLib.Error:
        return None
    if not pixbuf:
        return None
    width, height = pixbuf.get_width(), pixbuf.get_height()
    scale = AVATAR / min(width, height)
    width, height = max(AVATAR, round(width * scale)), max(AVATAR, round(height * scale))
    pixbuf = pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
    return pixbuf.new_subpixbuf((width - AVATAR) // 2, (height - AVATAR) // 2, AVATAR, AVATAR).copy()


def rounded(pixbuf: GdkPixbuf.Pixbuf, radius: float = 10) -> Gtk.Image:
    # GTK3 CSS border-radius doesn't clip images: clip with cairo instead
    size = pixbuf.get_width()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    for x, y, angle in ((size - radius, radius, -0.5), (size - radius, size - radius, 0), (radius, size - radius, 0.5), (radius, radius, 1)):
        cr.arc(x, y, radius, angle * 3.14159, (angle + 0.5) * 3.14159)
    cr.close_path()
    cr.clip()
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()
    return Gtk.Image.new_from_surface(surface)


IMG = re.compile(r"""<img\b[^>]*?\bsrc=["']([^"']+)["'][^>]*>""", re.I)


def body_image(body: str) -> tuple[str, GdkPixbuf.Pixbuf | None]:
    # body-images: <img src=.../> is not Pango markup, so cut the tags out and render the first local one
    match = IMG.search(body)
    body = IMG.sub("", body).strip()
    if not match:
        return body, None
    src = match.group(1)
    try:
        path = GLib.filename_from_uri(src)[0] if src.startswith("file://") else src
        return body, GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 340, 220, True)
    except GLib.Error:  # remote URL or unreadable file: the spec lets us skip it
        return body, None


LINK = re.compile(r"</?a\b[^>]*>", re.I)
URL = re.compile(r"""https?://[^\s<>"]*[^\s<>".,;:!?)\]]""")


def body_markup(body: str) -> str:
    # body-markup + body-hyperlinks: GtkLabel renders <a href> but Pango's parser rejects it,
    # so validate without the links; broken markup falls back to escaped text
    body = re.sub(r"&(?!#?\w+;)", "&amp;", body)  # senders often leave a bare & (URLs, "Tom & Jerry")
    try:
        Pango.parse_markup(LINK.sub("", body), -1, "\0")
        markup = body
    except GLib.Error:
        markup = GLib.markup_escape_text(body)
    # bare URLs in plain text become links too, unless the sender already marked them up
    return markup if LINK.search(markup) else URL.sub(lambda m: f'<a href="{m[0]}">{m[0]}</a>', markup)


def action_label(label: str) -> Gtk.Label:
    widget = text(label)
    # ellipsized with a tiny natural width: a long label shrinks its segment, never widens the card
    widget.set_ellipsize(Pango.EllipsizeMode.END)
    widget.set_max_width_chars(1)
    return widget


class NotificationCard(EventBox):
    def __init__(
        self,
        record: NotificationRecord,
        center: bool,
        activate: Callable[[NotificationRecord, str], None],
        close: Callable[[NotificationRecord], None],
    ):
        self.progress = progress = meter("thin", "notification-progress")
        progress.set_fraction(1)
        if center or record.persistent:  # never expires: nothing to count down
            progress.hide()
            progress.set_no_show_all(True)
        title = text(record.title, "notification-title", xalign=0)
        self.body = body = text("", "notification-body", xalign=0)
        body.set_markup(body_markup(record.body))
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
                        rounded(record.avatar) if record.avatar
                        else css(Gtk.Image.new_from_pixbuf(record.icon), "notification-icon") if record.icon
                        else text("󰂚", "notification-icon"),
                        Box(
                            orientation="v",
                            spacing=1,
                            h_expand=True,
                            children=[
                                Box(
                                    spacing=5,
                                    children=[
                                        # with an avatar in front, the app icon shrinks next to the app name
                                        *((Gtk.Image.new_from_pixbuf(record.icon.scale_simple(14, 14, GdkPixbuf.InterpType.BILINEAR)),) if record.avatar and record.icon else ()),
                                        text(record.app, "notification-app", xalign=0),
                                    ],
                                ),
                                title,
                            ],
                        ),
                        text(stamp(record.time), "notification-time"),
                        Button(label="×", style_classes=("notification-close",), on_clicked=lambda *_: close(record)),
                    ],
                ),
                body,
                *((css(Gtk.Image.new_from_pixbuf(record.image), "notification-image"),) if record.image else ()),
                *((Box(
                    spacing=2,  # hairline seams of card color: one control, separate targets
                    homogeneous=True,  # equal segments, whatever the label lengths
                    style_classes=("notification-buttons",),
                    children=[
                        Button(
                            child=Gtk.Image.new_from_icon_name(label, Gtk.IconSize.BUTTON) if record.icon_buttons else action_label(label),
                            h_expand=True,
                            style_classes=("notification-button",),
                            on_clicked=lambda *_, action=action: activate(record, action),
                        )
                        for action, label in record.buttons
                    ],
                ),) if record.buttons else ()),
                progress,
            ],
        )
        super().__init__(
            events="button-press",
            child=card,
            on_button_press_event=lambda *_: activate(record, record.action) or True,
        )


class NotificationHub:
    def __init__(self, monitor: Monitor, clock: ClockState, locked: Callable[[], bool]):
        self.locked = locked
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
                        Button(label="󰃢", tooltip_text="Clear all", style_classes=("notification-action",), on_clicked=lambda *_: self.clear()),
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

    def save(self) -> None:
        # coalesce bursts (clear all, a spammy sender) into one write
        if not mock.ENABLED and not self.save_timer:
            self.save_timer = GLib.timeout_add(500, self.flush)

    def flush(self) -> bool:
        self.save_timer = 0
        # the sender's actions die with this session: restored cards only dismiss
        data = [{**vars(record), "action": "", "buttons": [], **{key: png(getattr(record, key)) for key in PIXBUFS}} for record in self.records]
        HISTORY_FILE.write_text(json.dumps(data))
        return False

    def restore(self) -> None:
        try:
            data = json.loads(HISTORY_FILE.read_text())
        except (OSError, ValueError):
            return
        # oldest first, each lands on top; negative ids never clash with the bus's
        for index, item in enumerate(reversed(data)):
            try:
                self.add_history(NotificationRecord(**{**item, "id": -1 - index, **{key: unpng(item.get(key)) for key in PIXBUFS}}))
            except (TypeError, ValueError, GLib.Error):  # a record from an older schema
                continue

    def add(self, service: Notifications, notification_id: int) -> None:
        notification = service.get_notification_from_id(notification_id)
        if not notification:
            return
        # Fabric always allocates a new id, so honour replaces_id ourselves
        # (volume/progress notifications would otherwise pile up).
        if notification.replaces_id:
            service.remove_notification(notification.replaces_id)
            self.remove_record(notification.replaces_id)
        actions = [(action.identifier, action.label) for action in notification.actions]
        body, image = body_image(notification.body)
        record = NotificationRecord(
            notification.id,
            notification.app_name,
            notification.summary,
            body,
            notification.urgency,
            "default" if any(action == "default" for action, _ in actions) else "",
            notification.time,
            app_icon(notification),
            notification.urgency == 2 or notification.timeout == 0,
            image,
            avatar(notification),
            [(action, label) for action, label in actions if action != "default"],
            bool(notification.do_get_hint_entry("action-icons")),
        )
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
        self.remove_popup(notification_id)
        self.save()

    def activate(self, record: NotificationRecord, action: str) -> None:
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


def build(context: Any) -> list[Any]:
    context.notifications = NotificationHub(context.monitors[0], context.clock, lambda: bool(getattr(context, "lock", None) and context.lock.locked))
    if mock.ENABLED:
        context.notifications.seed_mock()
    return [context.notifications.popup_window, context.notifications.center_window]
