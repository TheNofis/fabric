"""Messages without GTK: phone numbers, day names, and what the search field finds."""

from __future__ import annotations

import re
from datetime import datetime

from services.tether import Thread


def day_label(moment: datetime, today: datetime) -> str:
    days = (today.date() - moment.date()).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    return f"{moment:%A}" if days < 7 else f"{moment:%A}, {moment.day} {moment:%B}" + ("" if moment.year == today.year else f" {moment.year}")


def phone(address: str) -> str:
    """+79220827679 → +7 922 082-76-79; anything else as it came."""
    digits = re.sub(r"\D", "", address)
    if len(digits) == 11 and digits[0] in "78":
        return f"{'+7' if digits[0] == '7' or address.startswith('+') else '8'} {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:]}"
    return address


def dialable(query: str) -> str | None:
    """A typed number to start a conversation with: 'tel:+79…', or None."""
    cleaned = re.sub(r"[\s()-]", "", query)
    return f"tel:{cleaned}" if re.fullmatch(r"\+?\d{5,15}", cleaned) else None


def search(query: str, threads: list[Thread], contacts: list[tuple[str, str]]) -> tuple[list[Thread], list[tuple[str, str]], str | None]:
    """(conversations, contacts to start one with, a typed number to start one with) for the search field."""
    query = query.strip().casefold()
    if not query:
        return threads, [], None
    digits = re.sub(r"\D", "", query)

    def hit(*values: str) -> bool:
        return any(query in value.casefold() for value in values) or (len(digits) >= 3 and any(digits in re.sub(r"\D", "", value) for value in values))
    found = [thread for thread in threads if hit(thread.name, thread.address, thread.preview)]
    known = {thread.id for thread in threads} | {f"tel:{thread.address}" for thread in threads}
    contacts = [(name, address) for name, address in contacts if address not in known and hit(name, address)][:6]
    number = dialable(query)
    return found, contacts, number if number and number not in known and all(number != address for _, address in contacts) else None


def check() -> None:
    from datetime import timedelta

    from shared.widgets import short_time

    today = datetime(2026, 10, 8, 21, 0)
    stamps = [today.replace(hour=9), today - timedelta(days=1), today - timedelta(days=3), today - timedelta(days=30), today - timedelta(days=400)]
    assert [short_time(s.timestamp(), today) for s in stamps] == ["09:00", "Yesterday", "Mon", "8 Sep", "3 Sep 2025"]
    assert [day_label(s, today) for s in stamps[:4]] == ["Today", "Yesterday", "Monday", "Tuesday, 8 September"]
    assert phone("+79220827679") == "+7 922 082-76-79" and phone("89264747777") == "8 926 474-77-77" and phone("megafon") == "megafon"
    assert dialable("+7 (922) 082-76-79") == "tel:+79220827679" and dialable("Глеб") is None and dialable("12") is None
    gleb = Thread("tel:+79220827679", "Глеб", "+79220827679", "see you", 0, 0, True)
    contacts = [("Глеб", "tel:+79220827679"), ("Anna", "tel:+79001112233")]
    assert search("", [gleb], contacts) == ([gleb], [], None)
    assert search("глеб", [gleb], contacts) == ([gleb], [], None)  # already a conversation: not offered again
    assert search("anna", [gleb], contacts) == ([], [("Anna", "tel:+79001112233")], None)
    assert search("922 082", [gleb], contacts)[0] == [gleb]  # digits match a formatted number
    assert search("+7 999 000 11 22", [gleb], contacts) == ([], [], "tel:+79990001122")
    print("messages format: ok")
