"""Claude plan limits: the bar's session gauge and its dropdown with both windows and reset times."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import cairo
from fabric.widgets.box import Box
from fabric.widgets.eventbox import EventBox
from gi.repository import Gtk

from modules.sysmon import detail_row, section
from services.monitors import Monitor
from services.state import JsonState
from services.system import ClockState
from shared.constants import SCRIPTS
from shared.ui import amount, big_value, header, meter, panel
from shared.widgets import css, flag, hover_reveal, slide, stat, text
from shared.window import BarPanel

SESSION = 5 * 3600
WEEK = 7 * 86400
ICON = "\U000F0AE2"  # nf-md-star_four_points
PLANS = {"pro": "Pro", "max": "Max", "team": "Team", "enterprise": "Enterprise"}


def current(limit: dict[str, Any] | None, now: float) -> tuple[int, int]:
    """(percent, seconds to reset); a window whose reset already passed is empty until the next poll."""
    if not limit:
        return 0, 0
    remaining = limit["resets"] - int(now)
    return (limit["pct"], remaining) if remaining > 0 else (0, 0)


def duration(seconds: int) -> str:
    """Two largest units: '1d 6h', '2h 14m', '45m'; under a minute rounds up."""
    minutes = -(-seconds // 60)
    days, hours, minutes = minutes // 1440, minutes // 60 % 24, minutes % 60
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def reset_at(timestamp: int, now: datetime) -> str:
    """'today 13:20', 'Wed 19:00' within the week."""
    moment = datetime.fromtimestamp(timestamp)
    return moment.strftime("today %H:%M" if moment.date() == now.date() else "%a %H:%M")


def level(limit: dict[str, Any] | None, pct: int) -> str:
    """'alert' at the cap or when the API calls it critical, 'warn' when it warns, else ''."""
    severity = (limit or {}).get("severity")
    return "alert" if pct >= 100 or severity == "critical" else "warn" if severity == "warning" and pct else ""


def paint(widget: Gtk.Widget, state: str) -> None:
    flag(widget, "warn", state == "warn")
    flag(widget, "alert", state == "alert")


class Gauge(Gtk.DrawingArea):
    """Usage bar with an optional even-pace tick: where usage would be if spent evenly over the
    window, so a fill past the tick runs out before the reset. Fill colour is CSS `color`."""

    def __init__(self, width: int = -1, bar: int = 4, tick: bool = True):
        super().__init__()
        self.fraction, self.pace, self.bar, self.tick = 0.0, 0.0, bar, tick
        css(self, "claude-gauge")
        self.set_size_request(width, bar + 8 if tick else bar)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self.on_draw)
        self.show()  # plain Gtk widget: not visible by default like Fabric's

    def set(self, fraction: float, pace: float = 0.0) -> None:
        self.fraction, self.pace = min(max(fraction, 0.0), 1.0), min(max(pace, 0.0), 1.0)
        self.queue_draw()

    def pill(self, cr: cairo.Context, width: float) -> None:
        radius, y = self.bar / 2, (self.get_allocated_height() - self.bar) / 2
        cr.new_sub_path()
        cr.arc(radius, y + radius, radius, 1.5708, 4.7124)
        cr.arc(width - radius, y + radius, radius, -1.5708, 1.5708)
        cr.close_path()

    def on_draw(self, _area: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        width, height = self.get_allocated_width(), self.get_allocated_height()
        self.pill(cr, width)
        cr.set_source_rgba(1, 1, 1, 0.18)  # .ui-meter trough
        cr.fill()
        if self.fraction:
            color = self.get_style_context().get_color(self.get_state_flags())
            self.pill(cr, max(width * self.fraction, self.bar))
            cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
            cr.fill()
        if self.tick and self.pace:
            x = round(min(max(width * self.pace, 1), width - 1)) - 1
            cr.rectangle(x, 0, 2, height)
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.fill()
        return False


def claude_slot(usage: JsonState, clock: ClockState, claude_panel: ClaudeWindow) -> EventBox:
    """Bar slot: session gauge and percent; hover slides out the time to the reset."""
    gauge = Gauge(28, tick=False)
    pct = text("", "value", "w-pct")
    left = text("", "muted")
    revealer = slide(left, "right")
    slot_stat = stat(ICON, gauge, pct, revealer)
    widget = hover_reveal(
        slot_stat,
        revealer,
        events=("button-press",),
        on_button_press_event=lambda *_: claude_panel.toggle_at(slot_stat) or True,
    )
    widget.set_no_show_all(True)  # hidden until the first numbers arrive

    def update(*_: Any) -> None:
        session = usage.value.get("session")
        widget.set_visible(bool(session))
        if not session:
            return
        widget.show_all()
        percent, remaining = current(session, clock.value.timestamp())
        gauge.set(percent / 100)
        pct.set_text(f"{percent}%")
        left.set_text(f"{duration(remaining)} left" if remaining else "reset")
        paint(slot_stat, level(session, percent))
        paint(gauge, level(session, percent))

    usage.subscribe(update)
    clock.subscribe(update)
    return widget


class ClaudeWindow(BarPanel):
    def __init__(self, monitor: Monitor, usage: JsonState, clock: ClockState):
        self.usage, self.clock = usage, clock
        self.windows: list[tuple[Any, ...]] = []
        sections = []
        for key, title, span, length in (("session", "Session", "5-hour window", SESSION), ("week", "Week", "7-day window", WEEK)):
            big, resets, left, gauge = big_value(), text("", "sysmon-value"), text("", "sysmon-value"), Gauge()
            box = section(title, text(span, "ui-detail"), big, [left], gauge, Box(style_classes=("sysmon-row",), children=[
                text("Resets", "sysmon-label"), Box(h_expand=True), resets,
            ]))
            self.windows.append((key, length, box, big, resets, left, gauge))
            sections.append(box)
        self.sources = Box(orientation="v", spacing=6)
        self.status = text("", "ui-detail", xalign=0)
        super().__init__(monitor, "claude", panel(*sections, self.sources, self.status))
        self.connect("show", lambda *_: self.update())
        usage.subscribe(lambda _value: self.update())
        clock.subscribe(lambda _now: self.update())

    def update(self) -> None:
        if not self.get_visible():
            return
        value, now = self.usage.value, self.clock.value
        for key, length, box, big, resets, left, gauge in self.windows:
            limit = value.get(key)
            box.set_visible(bool(limit))
            if not limit:
                continue
            pct, remaining = current(limit, now.timestamp())
            state = level(limit, pct)
            big.set_markup(amount(pct, "%"))
            paint(big, state)
            paint(gauge, state)
            gauge.set(pct / 100, 1 - remaining / length if remaining else 0)
            gauge.set_tooltip_text(f"Even pace: {round(100 - 100 * remaining / length)}%" if remaining else None)
            resets.set_text(reset_at(limit["resets"], now) if remaining else "now")
            left.set_text(f"{duration(remaining)} left" if remaining else "")

        sources = value.get("sources") or []
        rows = []
        for name, share in sources:
            bar = meter("thin")
            bar.set_fraction(share / 100)
            rows.append(detail_row(name, text(f"{share}%", "sysmon-value"), bar))
        self.sources.children = [header("Week by source"), *rows] if rows else []
        self.sources.set_visible(bool(rows))
        self.sources.show_all()

        plan = PLANS.get(value.get("plan", ""), "Claude")
        at = datetime.fromtimestamp(value["at"]).strftime("%H:%M") if value.get("at") else ""
        error = value.get("error")
        self.status.set_text(
            "Sign-in expired. Run claude to refresh it." if error == "login"
            else f"Can't reach Anthropic. Numbers from {at}." if error == "offline" and at
            else "Can't reach Anthropic." if error == "offline"
            else f"{plan} plan · updated {at}" if at
            else "Loading…"
        )


def build(context: Any) -> list[Any]:
    context.claude = JsonState(SCRIPTS / "claude.py", {})
    context.claude_panels = [ClaudeWindow(monitor, context.claude, context.clock) for monitor in context.monitors]
    return context.claude_panels
