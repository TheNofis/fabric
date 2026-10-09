"""Claude limits without GTK: what the numbers from usage.py mean."""

from __future__ import annotations

from datetime import datetime
from typing import Any



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


def check() -> None:
    assert current({"pct": 40, "resets": 1000}, 400) == (40, 600) and current({"pct": 40, "resets": 1000}, 1000) == (0, 0)
    assert [duration(s) for s in (1, 2700, 8040, 108000)] == ["1m", "45m", "2h 14m", "1d 6h"]
    assert [pace(p, r, 100) for p, r in ((50, 50), (52, 50), (60, 50), (38, 50))] == ["on pace", "on pace", "10% ahead of pace", "12% under pace"]
    assert [level({"severity": s}, p) for s, p in (("normal", 36), ("warning", 80), ("warning", 0), ("normal", 100), ("critical", 95))] == ["", "warn", "", "alert", "alert"]
