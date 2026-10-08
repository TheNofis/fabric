"""The notes store behind Dayline (Super+C): ~/.local/share/dayline/notes.json, iCloud bookkeeping included."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from modules.dayline.times import next_occurrence
from services.state import State
from services import mock

DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
DATA_DIR = DATA / "dayline"
NOTES_FILE = DATA_DIR / "notes.json"
OLD_NOTES_FILE = DATA / "fabric-shell" / "notes.json"  # before the rename to Dayline


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
                Note(6, today.isoformat(), "16:00", "Sprint review", list_id="work"),
            ]
            self.lists = [["mock", "Personal", "#4A90E2"], ["work", "Work", "#FF9500"]]
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


def check() -> None:
    import tempfile

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
    print("dayline store: ok")
