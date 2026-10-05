"""Calendar math, the notes store behind Dayline (Super+C) and its two-way iCloud Reminders sync."""

from __future__ import annotations

import calendar
import json
import os
import re
import sys
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from gi.repository import GLib

from services.state import State
from services import mock

DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
DATA_DIR = DATA / "dayline"
NOTES_FILE = DATA_DIR / "notes.json"
OLD_NOTES_FILE = DATA / "fabric-shell" / "notes.json"  # before the rename to Dayline


def month_start(day: date, shift: int = 0) -> date:
    index = day.year * 12 + day.month - 1 + shift
    return date(index // 12, index % 12 + 1, 1)


def grid_days(month: date) -> list[date]:
    """Six Monday-first weeks covering the month, so the panel never changes height."""
    first = month - timedelta(days=month.weekday())
    return [first + timedelta(days=i) for i in range(42)]


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
    first = month_start(day, months)
    return first.replace(day=min(day.day, calendar.monthrange(first.year, first.month)[1]))


def next_occurrence(day: date, repeat: list[int]) -> date:
    """The due date after `day` for an iCloud rule [frequency, interval]: 1 daily, 2 weekly, 3 monthly, 4 yearly."""
    frequency, interval = repeat
    if frequency in (1, 2):
        return day + timedelta(days=interval * (7 if frequency == 2 else 1))
    return add_months(day, interval * (12 if frequency == 4 else 1))


def stamp(moment: datetime | None = None) -> str:
    """UTC ISO time with a fixed shape, so last-write-wins compares strings."""
    return (moment or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")


KEEP_DONE = timedelta(days=7)  # completed notes stay visible (struck through) this long


@dataclass
class Note:
    id: int
    day: str  # ISO date, "" for an undated reminder
    time: str  # "HH:MM" or "" for an all-day note
    text: str
    fired: bool = False  # the reminder notification went out
    done: str = ""  # ISO date it was completed, "" while open
    desc: str = ""
    priority: int = 0  # Apple's: 0 none, 1 high, 5 medium, 9 low
    flagged: bool = False
    repeat: list[int] = field(default_factory=list)  # iCloud recurrence [frequency, interval], [] if none
    list_id: str = ""  # iCloud list, "" before the first sync
    remote_id: str = ""  # iCloud reminder, "" until pushed
    modified: str = ""  # stamp() of the last change, for last-write-wins
    dirty: bool = False  # changed here, iCloud has not heard yet
    deleted: bool = False  # tombstone kept until iCloud hears about the delete

    @property
    def when(self) -> datetime | None:
        return datetime.fromisoformat(f"{self.day}T{self.time}") if self.day and self.time else None

    def overdue(self, now: datetime) -> bool:
        """Open and its moment has passed: a timed note at its time, an all-day one once its day is over."""
        return bool(self.day and not self.done and (self.when <= now if self.when else self.day < now.date().isoformat()))


class Notes(State):
    """All notes, sorted by (day, time); emits the visible list after every change and saves it.

    `all` also holds tombstones and long-completed notes the panel no longer shows. The iCloud
    side (lists, the default list, the sync cursor) lives in the same file.
    """

    def __init__(self, path: Path = NOTES_FILE):
        self.path = path
        if mock.ENABLED:
            today = mock.NOW.date()
            self.all = [
                Note(1, today.isoformat(), "09:30", "Review release checklist", desc="README, screenshots and final commit", priority=5, list_id="mock"),
                Note(2, today.isoformat(), "14:00", "Ship the new shell panels", priority=1, list_id="mock"),
                Note(3, (today + timedelta(days=1)).isoformat(), "", "Team demo", flagged=True, list_id="mock"),
                Note(5, (today - timedelta(days=2)).isoformat(), "", "Renew the domain", priority=1, list_id="mock"),
                Note(4, "", "", "Ideas for the next iteration", desc="Keep the bar quiet and useful", list_id="mock"),
            ]
            self.lists = [["mock", "Personal", "#4A90E2"]]
            self.list = "mock"
            self.cursor = "mock"
            self.on_change: Callable[[], None] = lambda: None
            super().__init__(self.visible(self.all, today))
            return
        if path == NOTES_FILE and not path.exists() and OLD_NOTES_FILE.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            OLD_NOTES_FILE.replace(path)
        try:
            data = json.loads(path.read_text())
            data = {"notes": data} if isinstance(data, list) else data  # the pre-iCloud file was a bare list
            notes = [Note(**item) for item in data.get("notes", [])]
        except FileNotFoundError:
            data, notes = {}, []
        except (ValueError, TypeError, AttributeError) as error:
            # keep the broken file for the user instead of overwriting it with an empty list
            path.replace(path.with_suffix(".broken.json"))
            print(f"notes: {path} unreadable ({error}), moved aside", file=sys.stderr)
            data, notes = {}, []
        self.all = sorted(notes, key=self.order)
        self.lists: list[list[str]] = data.get("lists", [])  # [id, title, color] of every iCloud list
        self.list: str = data.get("list", "")  # where new notes go: the last list used
        self.cursor: str | None = data.get("cursor")  # iCloud sync token, None before the first sync
        self.on_change: Callable[[], None] = lambda: None  # a local edit: the syncer pushes it
        super().__init__(self.visible(self.all))

    @staticmethod
    def order(note: Note) -> tuple[str, str, int]:
        return note.day, note.time or "", note.id  # undated first, all-day notes lead their day

    @staticmethod
    def visible(notes: list[Note], today: date | None = None) -> list[Note]:
        cutoff = ((today or date.today()) - KEEP_DONE).isoformat()
        return [note for note in notes if not note.deleted and (not note.done or note.done >= cutoff)]

    def on(self, day: date | None) -> list[Note]:
        """Notes of a day; None: the undated ones."""
        key = day.isoformat() if day else ""
        return [note for note in self.value if note.day == key]

    def after(self, day: date, limit: int) -> list[Note]:
        return [note for note in self.value if note.day > day.isoformat()][:limit]

    def days(self) -> set[str]:
        return {note.day for note in self.value if note.day}

    def find(self, note_id: int) -> Note | None:
        return next((note for note in self.all if note.id == note_id), None)

    def add(self, day: date | None, time: str, text: str, now: datetime | None = None, **fields: Any) -> None:
        next_id = max((note.id for note in self.all), default=0) + 1
        note = Note(next_id, day.isoformat() if day else "", time if day else "", text, **fields)
        note.list_id = note.list_id or self.list
        self.list = note.list_id  # the next note goes to the same list
        self._save([*self.all, self._touched(self._armed(note, now))], local=True)

    def update(self, note_id: int, day: date | None, time: str, text: str, now: datetime | None = None, **fields: Any) -> None:
        """Changes the note; `fields` are other Note attributes (desc, priority, list_id)."""
        note = self.find(note_id)
        if not note:
            return
        key, time = (day.isoformat(), time) if day else ("", "")
        # a changed time re-arms the reminder
        changed = replace(note, day=key, time=time, text=text, **fields)
        if not (note.fired and (note.day, note.time) == (key, time)):
            changed = self._armed(changed, now)
        self._save([self._touched(changed) if item.id == note_id else item for item in self.all], local=True)

    def toggle_done(self, note_id: int, now: datetime | None = None) -> None:
        """Done/undone; a repeating note moves to its next date and stays open, like on the iPhone."""
        note = self.find(note_id)
        if not note:
            return
        today = (now or datetime.now()).date()
        if note.done:
            changed = replace(note, done="")
        elif note.repeat and note.day:
            changed = self._armed(replace(note, day=next_occurrence(date.fromisoformat(note.day), note.repeat).isoformat()), now)
        else:
            changed = replace(note, done=today.isoformat())
        self._save([self._touched(changed) if item.id == note_id else item for item in self.all], local=True)

    def delete(self, note_id: int) -> None:
        # an iCloud note leaves a tombstone, so an offline delete still reaches iCloud later
        self._save([
            self._touched(replace(note, deleted=True)) if note.remote_id else None
            for note in self.all if note.id == note_id
        ] + [note for note in self.all if note.id != note_id], local=True)

    @staticmethod
    def _touched(note: Note) -> Note:
        return replace(note, modified=stamp(), dirty=True)

    @staticmethod
    def _armed(note: Note, now: datetime | None) -> Note:
        """A note written for a time already gone is not announced; the current minute still is."""
        now = (now or datetime.now()).replace(second=0, microsecond=0)
        note.fired = bool(note.when and note.when < now)
        return note

    def due(self, now: datetime) -> list[Note]:
        """Notes whose time has come and that were not announced yet; marks them fired."""
        ready = [note for note in self.value if not note.fired and not note.done and note.when and note.when <= now]
        if ready:
            ids = {note.id for note in ready}
            self._save([replace(note, fired=True) if note.id in ids else note for note in self.all])
        return ready

    # --- iCloud side: the syncer calls these on the main loop ---

    def pending(self) -> list[Note]:
        return [replace(note) for note in self.all if note.dirty]

    def applied(self, pushed: list[tuple[int, str, Note | None]], changes: list[tuple[str, Note | None]],
                lists: list[list[str]], cursor: str, now: datetime | None = None) -> None:
        """Takes in one sync round: what was pushed ((id, its `modified` then, the reminder as a
        Note or None once deleted)), the iCloud changes ((reminder id, Note or None if deleted)),
        the lists and the new cursor."""
        first = self.cursor is None
        notes = {note.id: note for note in self.all}
        for note_id, modified, remote in pushed:
            note = notes.get(note_id)
            if not note:
                continue
            if note.deleted and note.modified == modified:
                del notes[note_id]
            elif remote:
                note.remote_id, note.list_id = remote.remote_id, remote.list_id
                note.dirty = note.modified != modified  # edited again while in flight: push again
        by_remote = {note.remote_id: note for note in notes.values() if note.remote_id}
        cutoff = ((now or datetime.now()).date() - KEEP_DONE).isoformat()
        for remote_id, remote in changes:
            note = by_remote.get(remote_id)
            if remote is None:
                if note and note.dirty and not note.deleted:
                    note.remote_id = ""  # deleted there, edited here: the edit comes back as a new reminder
                elif note:
                    del notes[note.id]
                continue
            if remote.done and remote.done < cutoff:
                if note and not note.dirty:
                    del notes[note.id]
                continue
            if note is None and first:  # first sync: a local note typed the same is that reminder
                note = next((n for n in notes.values() if not n.remote_id and (n.day, n.time, n.text) == (remote.day, remote.time, remote.text)), None)
            if note is None:
                note = self._armed(replace(remote, id=max(notes, default=0) + 1), now)
                notes[note.id] = by_remote[remote_id] = note
                continue
            if note.dirty and note.remote_id and note.modified > remote.modified:
                continue  # last write wins: ours is newer
            moved = (note.day, note.time) != (remote.day, remote.time)
            notes[note.id] = note = replace(remote, id=note.id, fired=note.fired)
            if moved:
                self._armed(note, now)
            by_remote[remote_id] = note
        self.lists, self.cursor = lists, cursor
        ids = [item[0] for item in lists]
        if ids and self.list not in ids:
            self.list = ids[0]
        for note in notes.values():  # notes made before the first sync go to the default list
            if ids and not note.remote_id and not note.deleted:
                note.dirty = True
                if note.list_id not in ids:
                    note.list_id = self.list
        self._save(list(notes.values()))
        if self.pending():
            self.on_change()

    def _save(self, notes: list[Note | None], local: bool = False) -> None:
        if mock.ENABLED:
            self.all = sorted((note for note in notes if note), key=self.order)
            self.emit(self.visible(self.all, mock.NOW.date()))
            return
        cutoff = (date.today() - KEEP_DONE).isoformat()
        # completed long ago and already in iCloud: forget it here
        notes = sorted((note for note in notes if note and not (note.done and note.done < cutoff and not note.dirty)), key=self.order)
        self.all = notes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        data = {"notes": [asdict(note) for note in notes], "lists": self.lists, "list": self.list, "cursor": self.cursor}
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        temp.replace(self.path)  # atomic: a crash mid-write never truncates the notes
        self.emit(self.visible(notes))
        if local:
            self.on_change()


# --- iCloud Reminders ---

ICLOUD_DIR = DATA_DIR / "icloud"  # session cookies; the password is in the keyring (pyicloud's entry)
ACCOUNT_FILE = ICLOUD_DIR / "account"  # the Apple ID; written by `python -m services.dayline login`
LOGIN_HINT = "run: ~/.config/fabric/.venv/bin/python -m services.dayline login"


class LoginNeeded(Exception):
    pass


def from_reminder(reminder: Any, repeat: list[int]) -> Note:
    due = reminder.due_date
    if due is None:
        day, time = "", ""
    elif reminder.all_day:
        # all-day is a midnight, UTC or local depending on the client: the nearest UTC day is the date
        day, time = (due.replace(tzinfo=due.tzinfo or UTC) + timedelta(hours=12)).astimezone(UTC).date().isoformat(), ""
    else:
        local = due.replace(tzinfo=due.tzinfo or UTC).astimezone()
        day, time = local.date().isoformat(), f"{local:%H:%M}"
    done = ""
    if reminder.completed:
        done = (reminder.completed_date or datetime.now(UTC)).astimezone().date().isoformat()
    return Note(0, day, time, reminder.title, done=done, desc=reminder.desc or "", priority=reminder.priority,
                flagged=reminder.flagged, repeat=repeat, list_id=reminder.list_id, remote_id=reminder.id,
                modified=stamp(reminder.modified) if reminder.modified else "")


def due_of(note: Note) -> tuple[datetime | None, bool]:
    """(due date, all day) for iCloud: a timed note is a local moment, an all-day one a UTC midnight."""
    if not note.day:
        return None, False
    if not note.time:
        return datetime.fromisoformat(note.day).replace(tzinfo=UTC), True
    return datetime.fromisoformat(f"{note.day}T{note.time}").astimezone(), False


def push(service: Any, note: Note, list_id: str, zone: str) -> Note | None:
    """Sends one local change; returns the reminder as a Note, None once deleted."""
    reminder = None
    if note.remote_id:
        try:
            reminder = service.get(note.remote_id)
        except LookupError:  # gone there already
            pass
        if reminder and reminder.deleted:  # iCloud soft-deletes: get() still returns it
            reminder = None
    if note.deleted:
        if reminder:
            service.delete(reminder)
        return None
    if reminder and reminder.list_id != list_id:
        # ponytail: moving lists is delete + create, the recurrence rule does not travel along
        service.delete(reminder)
        reminder = None
    due, all_day = due_of(note)
    if reminder is None:
        reminder = service.create(list_id=list_id, title=note.text, desc=note.desc, completed=bool(note.done), due_date=due,
                                  priority=note.priority, flagged=note.flagged, all_day=all_day, time_zone=None if all_day else zone)
    else:
        if reminder.completed != bool(note.done):
            reminder.completed_date = None  # set anew on completion
        reminder.title, reminder.desc, reminder.completed = note.text, note.desc, bool(note.done)
        reminder.due_date, reminder.all_day, reminder.time_zone = due, all_day, None if all_day else zone
        reminder.priority, reminder.flagged = note.priority, note.flagged
        service.update(reminder)
    return from_reminder(reminder, note.repeat)


def exchange(service: Any, pending: list[Note], cursor: str | None, default_list: str, zone: str):
    """One sync round against the Reminders service; runs off the main loop, touches no local state.
    Returns what Notes.applied() takes."""
    lists = [[item.id, item.title, item.color or ""] for item in service.lists() if not item.deleted and not item.is_group]
    ids = [item[0] for item in lists]
    fallback = default_list if default_list in ids else (ids[0] if ids else "")
    # the first round only reads: local notes are matched against iCloud before any is pushed
    pushed = [] if not ids or cursor is None else [
        (note.id, note.modified, push(service, note, note.list_id if note.list_id in ids else fallback, zone))
        for note in pending
    ]
    new_cursor = service.sync_cursor()  # taken before reading: a change racing the read comes again next round
    changes = []
    for event in service.iter_changes(since=cursor):
        reminder = event.reminder
        if reminder is None or reminder.deleted:
            changes.append((event.reminder_id, None))
            continue
        rules = service.recurrence_rules_for(reminder) if reminder.recurrence_rule_ids else []
        changes.append((reminder.id, from_reminder(reminder, [int(rules[0].frequency), rules[0].interval] if rules else [])))
    return pushed, changes, lists, new_cursor


def connect() -> Any:
    """The Reminders service of the saved account; LoginNeeded when a person has to step in."""
    from pyicloud import PyiCloudService
    from pyicloud.exceptions import PyiCloudFailedLoginException, PyiCloudNoStoredPasswordAvailableException

    try:
        api = PyiCloudService(ACCOUNT_FILE.read_text().strip(), cookie_directory=str(ICLOUD_DIR))
    except (PyiCloudFailedLoginException, PyiCloudNoStoredPasswordAvailableException) as error:
        raise LoginNeeded(str(error)) from error
    if api.requires_2fa or api.requires_2sa:
        raise LoginNeeded("the session expired, 2FA needed")
    return api.reminders


class Sync:
    """Two-way sync of Notes with iCloud Reminders: every minute, right after a local edit, and on demand.

    Network work runs in a thread; results come back to the main loop, the only place Notes changes.
    """

    def __init__(self, notes: Notes, notify: Callable[[str, str], None], connect: Callable[[], Any] = connect):
        self.notes, self.notify, self.connect = notes, notify, connect
        self.service: Any = None
        self.busy = self.again = False
        self.blocked: float | None = None  # account file mtime at a failed login: no retry until it changes
        notes.on_change = self.soon
        GLib.timeout_add_seconds(60, lambda: self.run() or True)
        self.run()

    def soon(self) -> None:
        GLib.timeout_add(1500, lambda: self.run() and False)  # a burst of edits goes out as one round

    def run(self) -> None:
        if not ACCOUNT_FILE.exists() or self.blocked == ACCOUNT_FILE.stat().st_mtime:
            return
        if self.busy:
            self.again = True
            return
        self.busy = True
        args = (self.notes.pending(), self.notes.cursor, self.notes.list)
        threading.Thread(target=self.work, args=args, daemon=True).start()

    def work(self, pending: list[Note], cursor: str | None, default_list: str) -> None:
        try:
            if self.service is None:
                self.service = self.connect()
            from tzlocal import get_localzone_name

            result = exchange(self.service, pending, cursor, default_list, get_localzone_name())
            GLib.idle_add(self.done, result, None)
        except Exception as error:  # noqa: BLE001 — offline, Apple hiccup, expired session: next round retries
            self.service = None
            GLib.idle_add(self.done, None, error)

    def done(self, result: tuple | None, error: Exception | None) -> bool:
        self.busy = False
        if result:
            self.notes.applied(*result)
        elif isinstance(error, LoginNeeded):
            self.blocked = ACCOUNT_FILE.stat().st_mtime
            self.notify("iCloud needs a login", f"{error}\n{LOGIN_HINT}")
        else:
            print(f"dayline sync: {type(error).__name__}: {error}", file=sys.stderr)
        if self.again:
            self.again = False
            self.run()
        return False


def login() -> None:
    """Interactive: Apple ID, password (kept in the keyring), the 2FA code. Sync picks it up within a minute."""
    from getpass import getpass

    from pyicloud import PyiCloudService
    from pyicloud.utils import store_password_in_keyring

    apple_id = input("Apple ID: ").strip()
    password = getpass("Password: ")
    api = PyiCloudService(apple_id, password, cookie_directory=str(ICLOUD_DIR))
    if api.requires_2fa:
        if not api.validate_2fa_code(input("2FA code: ").strip()):
            sys.exit("wrong code")
        if not api.is_trusted_session:
            api.trust_session()
    store_password_in_keyring(apple_id, password)
    ICLOUD_DIR.mkdir(parents=True, exist_ok=True)
    ACCOUNT_FILE.write_text(apple_id)
    print("lists:", ", ".join(item.title for item in api.reminders.lists()))


if __name__ == "__main__":
    if sys.argv[1:] == ["login"]:
        login()
        sys.exit()

    import tempfile

    assert month_start(date(2026, 12, 15), 1) == date(2027, 1, 1)
    assert month_start(date(2026, 1, 31), -1) == date(2025, 12, 1)
    days = grid_days(date(2026, 9, 1))
    assert days[0] == date(2026, 8, 31) and days[0].weekday() == 0 and len(days) == 42
    assert [parse_time(v) for v in ("", "9", "930", "9:30", "09.30", "23 59", "1405")] == ["", "09:00", "09:30", "09:30", "09:30", "23:59", "14:05"]
    assert [parse_time(v) for v in ("24", "9:60", "abc", "12345", "9:3")] == [None] * 5
    assert split_time("14:30 Call mom") == ("14:30", "Call mom") and split_time("Call mom 9.15") == ("09:15", "Call mom")
    assert split_time("Созвон в 9") == ("09:00", "Созвон") and split_time("Meet at 18:45") == ("18:45", "Meet")
    assert split_time("Buy 2 apples") == ("", "Buy 2 apples") and split_time("Room 25:00") == ("", "Room 25:00") and split_time("14:30") == ("", "14:30")
    at = datetime(2026, 9, 28, 14, 7)
    assert [step_time("", 15, at), step_time("", -15, at), step_time("", 15, datetime(2026, 9, 28, 14, 0))] == ["14:15", "14:00", "14:00"]
    assert [step_time("9:00", 15, at), step_time("09:00", -15, at), step_time("23:45", 15, at), step_time("00:00", -15, at)] == ["09:15", "08:45", "00:00", "23:45"]
    assert [step_time("9:07", 15, at), step_time("9:07", -15, at)] == ["09:15", "09:00"]  # off-grid snaps first

    path = Path(tempfile.mkdtemp()) / "notes.json"
    notes = Notes(path)
    today = date(2026, 9, 28)
    morning = datetime(2026, 9, 28, 8, 0)
    notes.add(today, "14:00", "Dentist", morning)
    notes.add(today, "", "Buy milk", morning)
    notes.add(today + timedelta(days=1), "09:30", "Standup", morning)
    notes.add(today, "07:00", "Written after the fact", morning)
    assert notes.on(today)[1].fired and notes.due(morning) == []  # a past time is not announced
    notes.delete(notes.on(today)[1].id)
    assert [n.text for n in notes.on(today)] == ["Buy milk", "Dentist"]
    assert [n.text for n in notes.after(today, 5)] == ["Standup"]
    assert [n.text for n in notes.due(datetime(2026, 9, 28, 14, 0))] == ["Dentist"]
    assert notes.due(datetime(2026, 9, 28, 14, 1)) == []  # fired once
    dentist = notes.on(today)[1]
    notes.update(dentist.id, today, "15:00", "Dentist", morning)  # moved later: reminder re-armed
    assert [n.text for n in notes.due(datetime(2026, 9, 28, 15, 0))] == ["Dentist"]
    notes.update(dentist.id, today + timedelta(days=2), "15:00", "Dentist, moved", morning)
    assert notes.on(today + timedelta(days=2))[0].fired is False
    notes.delete(dentist.id)
    assert [n.text for n in Notes(path).value] == ["Buy milk", "Standup"]  # persisted
    path.write_text("{broken")
    assert Notes(path).value == [] and path.with_suffix(".broken.json").exists()
    print("calendar service: ok")

    # --- sync against a fake Reminders service ---
    from copy import copy
    from types import SimpleNamespace

    class FakeReminders:
        """In-memory iCloud: a change log drives the cursor, like CloudKit's sync token."""

        def __init__(self):
            self.items: dict[str, SimpleNamespace] = {}
            self.log: list[str] = []
            self.lists_ = [SimpleNamespace(id=f"L{i}", title=title, color="", deleted=False, is_group=False) for i, title in enumerate(("Home", "Work"))]

        def lists(self):
            return self.lists_

        def get(self, rid):
            if rid not in self.items or self.items[rid].deleted:
                raise LookupError(rid)
            return copy(self.items[rid])

        def write(self, reminder, modified=None):  # also how a test plays the iPhone
            reminder.modified = modified or datetime.now(UTC)
            self.items[reminder.id] = copy(reminder)
            self.log.append(reminder.id)

        def create(self, list_id, title, desc="", completed=False, due_date=None, priority=0, flagged=False, all_day=False, time_zone=None, modified=None):
            reminder = SimpleNamespace(id=f"R{len(self.items) + 1}", list_id=list_id, title=title, desc=desc, completed=completed, completed_date=None,
                                       due_date=due_date, all_day=all_day, time_zone=time_zone, priority=priority, flagged=flagged, deleted=False, recurrence_rule_ids=[])
            self.write(reminder, modified)
            return reminder

        def update(self, reminder):
            self.write(reminder)

        def delete(self, reminder):
            reminder.deleted = True
            self.write(reminder)

        def sync_cursor(self):
            return str(len(self.log))

        def iter_changes(self, since=None):
            for rid in dict.fromkeys(self.log[int(since):]) if since is not None else list(self.items):
                yield SimpleNamespace(reminder_id=rid, reminder=copy(self.items[rid]))

        def recurrence_rules_for(self, reminder):
            return [SimpleNamespace(frequency=2, interval=1)]  # weekly

    def sync(store: Notes, cloud: FakeReminders) -> None:
        store.applied(*exchange(cloud, store.pending(), store.cursor, store.list, "UTC"))

    cloud = FakeReminders()
    path = Path(tempfile.mkdtemp()) / "notes.json"
    store = Notes(path)
    later = date.today() + timedelta(days=3)
    store.add(later, "09:30", "Standup")  # typed before the first login
    store.add(later, "", "Buy milk")
    cloud.create("L1", "Standup", due_date=datetime.fromisoformat(f"{later}T09:30").astimezone(), time_zone="UTC")
    cloud.create("L0", "Call mom")  # undated, made on the iPhone

    sync(store, cloud)  # first round: reads, matches Standup, pushes nothing
    assert len(cloud.items) == 2 and store.lists == [["L0", "Home", ""], ["L1", "Work", ""]] and store.list == "L0"
    standup = next(n for n in store.all if n.text == "Standup")
    assert standup.remote_id == "R1" and standup.list_id == "L1" and not standup.dirty
    assert [n.text for n in store.on(None)] == ["Call mom"]
    milk = next(n for n in store.all if n.text == "Buy milk")
    assert milk.dirty and milk.list_id == "L0" and not milk.remote_id

    sync(store, cloud)  # Buy milk goes up as an all-day reminder, its echo comes back unchanged
    milk = next(n for n in store.all if n.text == "Buy milk")
    assert milk.remote_id == "R3" and not milk.dirty and milk.day == later.isoformat() and milk.time == ""
    assert cloud.items["R3"].all_day and len(store.all) == 3

    # the iPhone completes Standup
    phone = cloud.get("R1")
    phone.completed, phone.completed_date = True, datetime.now(UTC)
    cloud.write(phone)
    sync(store, cloud)
    assert next(n for n in store.value if n.remote_id == "R1").done == date.today().isoformat()

    # edited on both sides: the later edit wins, whichever side it is
    store.update(milk.id, later, "", "Buy oat milk")
    phone = cloud.get("R3")
    phone.title = "Buy soy milk"
    cloud.write(phone, datetime.now(UTC) - timedelta(hours=1))  # older than the local edit
    sync(store, cloud)
    assert cloud.items["R3"].title == "Buy oat milk" and store.find(milk.id).text == "Buy oat milk"
    phone = cloud.get("R3")
    phone.title = "Buy milk, 2l"
    cloud.write(phone, datetime.now(UTC) + timedelta(hours=1))
    sync(store, cloud)
    assert store.find(milk.id).text == "Buy milk, 2l" and not store.find(milk.id).dirty

    # moved to the other list: a new reminder there, the old one deleted
    store.update(milk.id, later, "", "Buy milk, 2l", list_id="L1")
    sync(store, cloud)
    moved = store.find(milk.id)
    assert moved.list_id == "L1" and moved.remote_id != "R3" and cloud.items["R3"].deleted and cloud.items[moved.remote_id].list_id == "L1"

    # deleted here: a tombstone until iCloud has it, then gone
    store.delete(moved.id)
    assert store.find(moved.id).deleted and moved.id not in {n.id for n in store.value}
    sync(store, cloud)
    assert store.find(moved.id) is None and cloud.items[moved.remote_id].deleted

    # deleted on the iPhone
    mom = store.on(None)[0]
    cloud.delete(cloud.get(mom.remote_id))
    sync(store, cloud)
    assert store.find(mom.id) is None

    # a weekly reminder: done here moves it a week on and keeps it open
    weekly = cloud.create("L0", "Gym", due_date=datetime.fromisoformat(f"{later}T18:00").astimezone(), time_zone="UTC")
    weekly.recurrence_rule_ids = ["rule"]
    cloud.write(weekly)
    sync(store, cloud)
    gym = next(n for n in store.value if n.text == "Gym")
    assert gym.repeat == [2, 1] and gym.time == "18:00"
    store.toggle_done(gym.id)
    assert store.find(gym.id).day == (later + timedelta(days=7)).isoformat() and not store.find(gym.id).done
    sync(store, cloud)
    assert cloud.items[gym.remote_id].due_date.astimezone().date() == later + timedelta(days=7) and not cloud.items[gym.remote_id].completed

    # completed over a week ago: not shown, then forgotten
    old = cloud.create("L0", "Old task", completed=True)
    old.completed_date = datetime.now(UTC) - timedelta(days=9)
    cloud.write(old)
    sync(store, cloud)
    assert all(n.text != "Old task" for n in store.all)
    assert Notes(path).cursor == store.cursor and Notes(path).lists == store.lists  # persisted
    assert [next_occurrence(date(2026, 1, 31), rule) for rule in ([3, 1], [4, 1], [1, 2])] == [date(2026, 2, 28), date(2027, 1, 31), date(2026, 2, 2)]
    print("dayline sync: ok")
