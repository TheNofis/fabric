"""What a notification is to the shell: the record, how one is read off the bus, and the saved history."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import Any

from gi.repository import GdkPixbuf, GLib, Gtk

from services.tether import code_in
from shared.constants import RUNTIME


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


def from_notification(notification: Any) -> NotificationRecord:
    actions = [(action.identifier, action.label) for action in notification.actions]
    body, image = body_image(notification.body)
    buttons = [(action, label) for action, label in actions if action != "default"]
    code = code_in(f"{notification.summary} {body}") if any(action in ("reply", "copy-code") for action, _ in buttons) else None
    if code:  # a text with a one-time code from the iPhone: the code is the only thing to act on
        buttons = [("copy-code", f"Copy {code}"), *((action, label) for action, label in buttons if action not in ("reply", "copy-code"))]
    return NotificationRecord(
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
        buttons,
        bool(notification.do_get_hint_entry("action-icons")),
    )


def save(records: list[NotificationRecord]) -> None:
    # the sender's actions die with this session: restored cards only dismiss
    data = [{**vars(record), "action": "", "buttons": [], **{key: png(getattr(record, key)) for key in PIXBUFS}} for record in records]
    HISTORY_FILE.write_text(json.dumps(data))


def load() -> list[NotificationRecord]:
    """The saved history, oldest first; negative ids never clash with the bus's."""
    try:
        data = json.loads(HISTORY_FILE.read_text())
    except (OSError, ValueError):
        return []
    records = []
    for index, item in enumerate(reversed(data)):
        try:
            records.append(NotificationRecord(**{**item, "id": -1 - index, **{key: unpng(item.get(key)) for key in PIXBUFS}}))
        except (TypeError, ValueError, GLib.Error):  # a record from an older schema
            continue
    return records
