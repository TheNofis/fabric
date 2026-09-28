"""Calendar popup: one per monitor, opened from the bar clock."""

from __future__ import annotations

from typing import Any

from fabric.widgets.box import Box
from gi.repository import Gtk

from services.monitors import Monitor
from services.system import ClockState
from shared.constants import CONTENT_GAP, POPUP_TOP
from shared.widgets import css, text
from shared.window import PopupWindow


class CalendarWindow(PopupWindow):
    def __init__(self, monitor: Monitor, clock: ClockState):
        self.title_label = text("", "popup-title", xalign=0)
        calendar = Gtk.Calendar()
        css(calendar, "cal")
        super().__init__(
            monitor,
            title=f"fabric-calendar-{monitor.name}",
            geometry="top-left",
            margin=f"{POPUP_TOP}px 0px 0px {-CONTENT_GAP}px",
            focusable=False,
            child=Box(
                orientation="v",
                spacing=8,
                style_classes=("popup",),
                children=[self.title_label, calendar],
            ),
        )
        self.clip_to(12, self.get_child())
        clock.subscribe(lambda now: self.title_label.set_text(now.strftime("%A, %d %B %Y")))


def build(context: Any) -> list[Any]:
    context.calendars = [
        CalendarWindow(monitor, context.clock)
        for monitor in context.monitors
    ]
    return context.calendars
