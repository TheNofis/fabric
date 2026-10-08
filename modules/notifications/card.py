"""One notification card, used both as a popup and in the center's history."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
import cairo
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from modules.notifications.record import NotificationRecord
from shared.ui import meter
from shared.widgets import css, line, short_time, text, wrapped


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


class NotificationCard(EventBox):
    def __init__(
        self,
        record: NotificationRecord,
        center: bool,
        activate: Callable[[NotificationRecord, str], None],
        close: Callable[[NotificationRecord], None],
    ):
        self.progress = progress = meter("thin", "notification-progress", glide=False)
        progress.set_fraction(1)
        if center or record.persistent:  # never expires: nothing to count down
            progress.hide()
            progress.set_no_show_all(True)
        title = wrapped(record.title, "notification-title", chars=32)
        self.body = body = wrapped("", "notification-body", chars=45)
        body.set_markup(body_markup(record.body))
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
                        text(short_time(record.time, datetime.now()), "notification-time"),
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
                            # a long label shrinks its segment, never widens the card
                            child=Gtk.Image.new_from_icon_name(label, Gtk.IconSize.BUTTON) if record.icon_buttons else line(label, xalign=0.5),
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
