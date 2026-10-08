"""iPhone messages through tetherd's JSON socket: threads, a thread's messages, contacts, replies.

Requests are one JSON line per connection, answered with one line. A long-lived `subscribe`
connection streams events (new and read messages, link changes); any message event refetches
the threads and tells the open conversation to reload. Socket work runs on worker threads and
hands results back on the GTK loop, so the panel never waits on Bluetooth.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from gi.repository import GLib

from services import mock
from services.state import State

SOCKET = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "tether" / "tetherd.sock"
CODE = re.compile(r"(?<![\d+])(\d{4,8}|\d{3}[- ]\d{3})(?!\d)")
CODE_WORDS = re.compile(r"code|код|пароль|password|pin|otp|verif|подтвержд", re.I)


@dataclass(frozen=True)
class Thread:
    id: str  # "tel:+7..." or "sender:apple"
    name: str
    address: str
    preview: str
    time: float
    unread: int
    repliable: bool


@dataclass(frozen=True)
class Message:
    handle: str
    body: str
    outgoing: bool
    read: bool
    time: float


def code_in(body: str) -> str | None:
    """A one-time code: 4-8 digits in a message that says it is a code."""
    if not CODE_WORDS.search(body):
        return None
    match = CODE.search(body)
    return match[1].replace(" ", "").replace("-", "") if match else None


def initials(name: str) -> str:
    words = [word for word in re.split(r"[\s._-]+", name) if word[:1].isalpha()]
    return "".join(word[0] for word in words[:2]).upper()


def _request(command: dict[str, Any], timeout: float = 8) -> Any:
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(timeout)
        sock.connect(str(SOCKET))
        sock.sendall(json.dumps(command).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(1 << 20)
            if not chunk:
                break
            data += chunk
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data.decode(errors="replace").strip()  # mark_read answers a bare OK


def _thread(item: dict[str, Any]) -> Thread:
    return Thread(item["thread"], item.get("name") or item.get("address") or item["thread"], item.get("address", ""),
                  item.get("preview", ""), item.get("timestamp", 0), item.get("unread", 0), bool(item.get("repliable")))


def _message(item: dict[str, Any]) -> Message:
    return Message(item.get("handle", ""), item.get("body", ""), bool(item.get("outgoing")), bool(item.get("read", True)), item.get("timestamp", 0))


class Tether(State):
    """value: {"threads": [Thread], "online": bool (iPhone linked for messages), "daemon": bool, "loaded": bool}."""

    def __init__(self):
        super().__init__({"threads": [], "online": False, "daemon": True, "loaded": False})
        self.contacts: list[tuple[str, str]] = []  # (name, "tel:...")
        self.changed: list[Callable[[], None]] = []  # message events: the open conversation reloads
        self.debounce = 0
        if mock.ENABLED:
            now = mock.NOW.timestamp()
            self.emit({"threads": [Thread(*row[:3], row[3], now - row[4], *row[5:]) for row in _MOCK_THREADS], "online": True, "daemon": True, "loaded": True})
            self.contacts = _MOCK_CONTACTS
            return
        threading.Thread(target=self._listen, daemon=True).start()

    def _set(self, **fields: Any) -> None:
        self.emit({**self.value, **fields})

    def _work(self, job: Callable[[], Any], done: Callable[[Any], None]) -> None:
        def run() -> None:
            try:
                result = job()
            except (OSError, ValueError, KeyError) as error:
                result = error
            GLib.idle_add(lambda: done(result) and False)
        threading.Thread(target=run, daemon=True).start()

    def refresh(self) -> None:
        if mock.ENABLED:
            return

        def done(result: Any) -> None:
            if isinstance(result, dict) and "threads" in result:
                threads = sorted((_thread(item) for item in result["threads"]), key=lambda t: -t.time)
                self._set(threads=threads, loaded=True, daemon=True)
            else:
                self._set(loaded=True, daemon=not isinstance(result, (FileNotFoundError, ConnectionRefusedError)))
        self._work(lambda: _request({"command": "bt_list_threads"}), done)

    def messages(self, thread: str, done: Callable[[list[Message] | None], None]) -> None:
        if mock.ENABLED:
            done([Message(str(i), body, out, True, mock.NOW.timestamp() - ago) for i, (out, ago, body) in enumerate(_MOCK_MESSAGES.get(thread, []))])
            return

        def parse(result: Any) -> None:
            ok = isinstance(result, dict) and "messages" in result
            done(sorted((_message(item) for item in result["messages"]), key=lambda m: m.time) if ok else None)
        self._work(lambda: _request({"command": "bt_list_messages", "thread": thread}), parse)

    def load_contacts(self) -> None:
        if self.contacts or mock.ENABLED:
            return

        def done(result: Any) -> None:
            if isinstance(result, dict):
                self.contacts = [(item.get("name", ""), address) for item in result.get("contacts", []) for address in item.get("addresses", [])
                                 if address.startswith("tel:")]
        self._work(lambda: _request({"command": "bt_list_contacts"}, timeout=15), done)

    def mark_read(self, thread: str, handles: list[str]) -> None:
        if handles and not mock.ENABLED:
            self._work(lambda: _request({"command": "bt_mark_read", "thread": thread, "handles": handles}), lambda _: self.refresh())

    def send(self, thread: str, body: str, done: Callable[[bool], None]) -> None:
        """The result comes as a bt_send_result event, so subscribe on the same connection first, like the CLI."""
        if mock.ENABLED:
            GLib.timeout_add(700, lambda: done(True) and False)
            return

        def job() -> bool:
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(45)  # the iPhone answers over MAP; a slow link takes a while
                sock.connect(str(SOCKET))
                sock.sendall(b'{"command":"subscribe"}\n' + json.dumps({"command": "bt_send_message", "thread": thread, "body": body}).encode() + b"\n")
                for line in sock.makefile(encoding="utf-8", errors="replace"):
                    event = json.loads(line) if line.startswith("{") else {}
                    if event.get("command") == "bt_send_result":
                        return bool(event.get("success"))
            return False
        self._work(job, lambda result: done(result is True))

    def _listen(self) -> None:
        """The event stream; reconnects when tetherd restarts."""
        while True:
            try:
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.connect(str(SOCKET))
                    sock.sendall(b'{"command":"subscribe"}\n')
                    GLib.idle_add(lambda: self.refresh() or False)
                    for line in sock.makefile(encoding="utf-8", errors="replace"):
                        if line.startswith("{"):
                            event = json.loads(line)
                            GLib.idle_add(lambda event=event: self._on_event(event) or False)
            except (OSError, ValueError):
                pass
            GLib.idle_add(lambda: self._set(online=False, daemon=SOCKET.exists()) or False)
            threading.Event().wait(5)

    def _on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("command", "")
        if kind == "bt_connection_changed":
            self._set(online=bool(event.get("classic_connected") and event.get("map_open")), daemon=True)
        elif kind.startswith("bt_message") or kind in ("bt_threads", "bt_send_result"):
            if self.debounce:
                GLib.source_remove(self.debounce)
            self.debounce = GLib.timeout_add(250, self._changed)

    def _changed(self) -> bool:
        self.debounce = 0
        self.refresh()
        for callback in self.changed:
            callback()
        return False


_MOCK_THREADS = [  # id, name, address, preview, seconds ago, unread, repliable
    ("tel:+79220827679", "Глеб Матвеев", "+79220827679", "Тогда в семь у входа, я возьму билеты", 240, 2, True),
    ("sender:apple", "Apple", "apple", "Your Apple Account Code is: 136438. Don't share it with anyone.", 900, 1, False),
    ("tel:+79617417888", "Аня", "+79617417888", "Скинь потом фотки с выходных", 5400, 0, True),
    ("sender:megafon", "MegaFon", "megafon", "Внимание! Мошенники могут звонить и представляться сотрудниками банка", 86400 + 3600, 6, False),
    ("tel:+79503045003", "Брат Тимофей", "+79503045003", "Ок, наберу вечером", 2 * 86400, 0, True),
    ("sender:cloud.ru", "Cloud.ru", "cloud.ru", "7110 - код подтверждения Cloud.ru", 5 * 86400, 0, False),
    ("tel:+79818656418", "+7 981 865-64-18", "+79818656418", "ИП Цыганко, звонил вам. Перезвоните, пожалуйста", 8 * 86400, 1, True),
]
_MOCK_MESSAGES = {  # thread: [(outgoing, seconds ago, body)]
    "tel:+79220827679": [
        (False, 86400 + 7200, "Ты в субботу свободен?"),
        (True, 86400 + 7000, "Да, после обеда"),
        (True, 86400 + 6990, "А что планируешь?"),
        (False, 86400 + 6000, "Концерт в «Космонавте», есть два билета"),
        (True, 600, "Во сколько начало?"),
        (False, 300, "В восемь, но лучше прийти пораньше"),
        (False, 240, "Тогда в семь у входа, я возьму билеты"),
    ],
    "tel:+79617417888": [(i % 3 == 0, 3 * 86400 - i * 1500, line) for i, line in enumerate(  # long enough to scroll
        ["Привет! Как съездили?", "Отлично, погода была супер", "Фоток куча, потом скину", "Где жили в итоге?",
         "В том домике у озера, который ты советовала", "Утром туман над водой, очень красиво", "Завидую!",
         "В следующий раз поехали вместе", "Давай, в октябре как раз будет свободнее", "Записала",
         "Кстати, ты книгу вернула в библиотеку?", "Ой, нет, завтра занесу", "Не забудь, там уже штраф капает"] * 3)]
    + [(False, 5400, "Скинь потом фотки с выходных")],
    "sender:apple": [(False, 3 * 86400, "Your Apple Account Code is: 297979. Don't share it with anyone."), (False, 900, "Your Apple Account Code is: 136438. Don't share it with anyone.")],
}
_MOCK_CONTACTS = [("Аня", "tel:+79617417888"), ("Артём Пузо", "tel:+79020113747"), ("Бабушка", "tel:+79930336379"), ("Брат Тимофей", "tel:+79503045003"), ("Глеб Матвеев", "tel:+79220827679")]


if __name__ == "__main__":
    assert code_in("Your Apple Account Code is: 136438. Don't share it with anyone.") == "136438"
    assert code_in("7110 - код подтверждения Cloud.ru") == "7110"
    assert code_in("Код 123-456 для входа") == "123456"
    assert code_in("Встречаемся в 1900 у входа") is None and code_in("Your code is ready") is None
    assert code_in("Pay +79220827679, code 4821") == "4821"
    assert initials("Глеб Матвеев") == "ГМ" and initials("MegaFon") == "M" and initials("+7 981 865") == "" and initials("cloud.ru") == "CR"
    print("tether self-check: ok")
