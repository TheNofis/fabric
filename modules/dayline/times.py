"""Dayline's date and time math: typed times, the quarter-hour stepper, recurrence and day names. No GTK."""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta


def parse_time(value: str) -> str | None:
    """'9' / '930' / '9:30' / '09.30' -> '09:30'; '' -> '' (no time); garbage -> None."""
    value = value.strip()
    if not value:
        return ""
    match = re.fullmatch(r"(\d{1,2})(?:[:.\s]?(\d{2}))?", value)
    if not match:
        return None
    hour, minute = int(match[1]), int(match[2] or 0)
    return f"{hour:02}:{minute:02}" if hour < 24 and minute < 60 else None


def split_time(value: str) -> tuple[str, str]:
    """Time typed into the note itself: '14:30 Call', 'Call 14:30', 'Call в 9', 'Call at 9:15'.

    A bare number needs 'в'/'at' in front, so 'Buy 2 apples' keeps its 2. Returns (time, text), time '' if none.
    """
    value = value.strip()
    for pattern in (r"(\d{1,2}[:.]\d{2})\s+(?P<rest>.+)", r"(?P<rest>.+?)\s+(?:(?:в|at)\s+)?(\d{1,2}[:.]\d{2})", r"(?P<rest>.+?)\s+(?:в|at)\s+(\d{1,2})"):
        match = re.fullmatch(pattern, value, re.IGNORECASE)
        if match and (time := parse_time(next(g for g in match.groups() if g != match["rest"]))):
            return time, match["rest"].strip()
    return "", value


def step_time(value: str, minutes: int, now: datetime) -> str:
    """Arrow/wheel on the time: an empty or off-grid time first snaps to the quarter hour
    (empty starts from now), then steps; wraps at midnight."""
    time = parse_time(value)
    total = int(time[:2]) * 60 + int(time[3:]) if time else now.hour * 60 + now.minute
    if time and total % 15 == 0:
        total += minutes
    else:
        total = -(-total // 15) * 15 if minutes > 0 else total // 15 * 15
    total %= 24 * 60
    return f"{total // 60:02}:{total % 60:02}"


def add_months(day: date, months: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def next_occurrence(day: date, repeat: list[int]) -> date:
    """The due date after `day` for an iCloud rule [frequency, interval]: 1 daily, 2 weekly, 3 monthly, 4 yearly."""
    frequency, interval = repeat
    if frequency in (1, 2):
        return day + timedelta(days=interval * (7 if frequency == 2 else 1))
    return add_months(day, interval * (12 if frequency == 4 else 1))


def relative(day: date, today: date) -> str:
    delta = (day - today).days
    return {0: "Today", 1: "Tomorrow", -1: "Yesterday"}.get(delta) or (f"In {delta} days" if delta > 0 else f"{-delta} days ago")


def check() -> None:
    assert [parse_time(v) for v in ("", "9", "930", "9:30", "09.30", "23 59", "1405")] == ["", "09:00", "09:30", "09:30", "09:30", "23:59", "14:05"]
    assert [parse_time(v) for v in ("24", "9:60", "abc", "12345", "9:3")] == [None] * 5
    assert split_time("14:30 Call mom") == ("14:30", "Call mom") and split_time("Call mom 9.15") == ("09:15", "Call mom")
    assert split_time("Созвон в 9") == ("09:00", "Созвон") and split_time("Meet at 18:45") == ("18:45", "Meet")
    assert split_time("Buy 2 apples") == ("", "Buy 2 apples") and split_time("Room 25:00") == ("", "Room 25:00") and split_time("14:30") == ("", "14:30")
    at = datetime(2026, 9, 28, 14, 7)
    assert [step_time("", 15, at), step_time("", -15, at), step_time("", 15, datetime(2026, 9, 28, 14, 0))] == ["14:15", "14:00", "14:00"]
    assert [step_time("9:00", 15, at), step_time("09:00", -15, at), step_time("23:45", 15, at), step_time("00:00", -15, at)] == ["09:15", "08:45", "00:00", "23:45"]
    assert [step_time("9:07", 15, at), step_time("9:07", -15, at)] == ["09:15", "09:00"]  # off-grid snaps first
    today = date(2026, 9, 28)
    assert [relative(today + timedelta(days=d), today) for d in (0, 1, -1, 3, -4)] == ["Today", "Tomorrow", "Yesterday", "In 3 days", "4 days ago"]
    assert [next_occurrence(date(2026, 1, 31), rule) for rule in ([3, 1], [4, 1], [1, 2])] == [date(2026, 2, 28), date(2027, 1, 31), date(2026, 2, 2)]
    print("dayline times: ok")
