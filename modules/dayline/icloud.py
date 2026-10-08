"""Two-way sync of the Dayline notes with iCloud Reminders (pyicloud).

Log in once: ~/.config/fabric/.venv/bin/python -m modules.dayline.icloud login
"""

from __future__ import annotations

import sys
import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable

from gi.repository import GLib

from modules.dayline.store import DATA_DIR, Note, Notes, stamp

ICLOUD_DIR = DATA_DIR / "icloud"  # session cookies; the password is in the keyring (pyicloud's entry)
ACCOUNT_FILE = ICLOUD_DIR / "account"  # the Apple ID; written by `python -m modules.dayline.icloud login`
LOGIN_HINT = "run: ~/.config/fabric/.venv/bin/python -m modules.dayline.icloud login"


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
    from pathlib import Path

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
    print("dayline sync: ok")
