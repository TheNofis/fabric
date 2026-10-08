"""Claude plan limits: the bar's session gauge and its dropdown with both windows and reset times."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.eventbox import EventBox
from gi.repository import Gtk

from services.monitors import Monitor
from services.state import JsonState
from services.system import ClockState
from shared.constants import SCRIPTS
from shared.ui import amount, big_value, detail_row, header, meter, panel, section, stat_value
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


def pace(pct: int, remaining: int, length: int) -> str:
    """Usage against even spending over the window: '8% ahead of pace', 'on pace' (within 3), '12% under pace'."""
    gap = round(pct - 100 + 100 * remaining / length)
    return "on pace" if abs(gap) <= 3 else f"{gap}% ahead of pace" if gap > 0 else f"{-gap}% under pace"


def level(limit: dict[str, Any] | None, pct: int) -> str:
    """'alert' at the cap or when the API calls it critical, 'warn' when it warns, else ''."""
    severity = (limit or {}).get("severity")
    return "alert" if pct >= 100 or severity == "critical" else "warn" if severity == "warning" and pct else ""


def paint(widget: Gtk.Widget, state: str) -> None:
    flag(widget, "warn", state == "warn")
    flag(widget, "alert", state == "alert")


class PaceMeter(Gtk.Overlay):
    """meter() with an even-pace tick: where usage would be if spent evenly over the window,
    so a fill past the tick runs out before the reset."""

    def __init__(self):
        super().__init__()
        self.pace = 0.0
        self.bar = meter()
        self.tick = css(Gtk.Box(), "claude-pace")
        self.tick.set_no_show_all(True)  # set() owns its visibility
        self.add(self.bar)
        self.add_overlay(self.tick)
        self.set_size_request(-1, 12)  # the tick overhangs the 4px bar
        self.connect("get-child-position", self.place)
        self.show_all()

    def set(self, fraction: float, pace: float) -> None:
        self.bar.set_fraction(min(max(fraction, 0.0), 1.0))
        self.pace = min(max(pace, 0.0), 1.0)
        self.tick.set_visible(bool(self.pace))
        self.queue_resize()

    def place(self, _overlay: Gtk.Overlay, _tick: Gtk.Widget, rect: Any) -> bool:
        width = self.get_allocated_width()
        rect.x, rect.y = round(min(max(width * self.pace, 1), width - 1)) - 1, 0
        rect.width, rect.height = 2, self.get_allocated_height()
        return True


def claude_slot(usage: JsonState, clock: ClockState, claude_panel: ClaudeWindow) -> EventBox:
    """Bar slot: session gauge and percent; hover slides out the time to the reset."""
    gauge = meter()
    gauge.set_hexpand(False)
    gauge.set_size_request(28, -1)
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
        gauge.set_fraction(min(percent / 100, 1))
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
            big, resets, left, gauge = big_value(), stat_value(), stat_value(), PaceMeter()
            box = section(title, text(span, "ui-detail"), big, [left], gauge, detail_row("Resets", resets))
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
            paint(gauge.bar, state)
            gauge.set(pct / 100, 1 - remaining / length if remaining else 0)
            gauge.set_tooltip_text(f"Even pace: {round(100 - 100 * remaining / length)}%" if remaining else None)
            resets.set_text(f"{reset_at(limit['resets'], now)} · in {duration(remaining)}" if remaining else "now")
            left.set_text(pace(pct, remaining, length) if remaining else "")

        sources = value.get("sources") or []
        rows = []
        for name, share in sources:
            bar = meter("thin")
            bar.set_fraction(share / 100)
            rows.append(detail_row(name, stat_value(f"{share}%"), bar))
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
