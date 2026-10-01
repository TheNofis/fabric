"""Launcher sources: each turns a query into rows (apps, calculator, commands, power, clipboard, emoji)."""

from __future__ import annotations

import ast
import json
import math
import operator
import re
import subprocess
import sys
import tomllib
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import Any
from urllib.parse import quote_plus, urlparse

from shared.constants import ROOT, RUNTIME
from shared.widgets import copy_text, run
from services import mock

MAX_RESULTS = 7
TERMINAL = "st"
BROWSER = "chromium"
SEARCH_URL = "https://www.google.com/search?q="
SITES_FILE = ROOT / "sites.toml"
USAGE_FILE = RUNTIME / "launcher.json"


@dataclass
class Item:
    icon: Any  # glyph str or GdkPixbuf
    label: str
    action: Callable[[], Any]
    confirm: bool = False  # destructive: first activation only asks
    detail: str = ""  # second line under the label (an ssh host's target)


# --- calculator --------------------------------------------------------------

_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
           ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_MATH = {name: value for name, value in vars(math).items() if not name.startswith("_")}


def _eval(node: ast.AST) -> float:
    match node:
        case ast.Constant(value=int() | float() as value):
            return value
        case ast.BinOp(left, op, right) if type(op) in _BINARY:
            left, right = _eval(left), _eval(right)
            if isinstance(op, ast.Pow) and abs(right) > 1000:
                raise ValueError("exponent too large")  # 9**9**9 would hang the shell
            return _BINARY[type(op)](left, right)
        case ast.UnaryOp(op, operand) if type(op) in _UNARY:
            return _UNARY[type(op)](_eval(operand))
        case ast.Name(id=name) if isinstance(_MATH.get(name), float):
            return _MATH[name]
        case ast.Call(func=ast.Name(id=name), args=args, keywords=[]) if callable(_MATH.get(name)):
            # ponytail: factorial(10**6) can still stall for seconds; cap args if that ever bites
            return _MATH[name](*map(_eval, args))
    raise ValueError("unsupported expression")


def calc(query: str) -> str | None:
    """Result of an arithmetic query ("2+2*3", "sqrt(2)^2", "10 % 3") or None."""
    try:
        tree = ast.parse(query.strip().replace("^", "**").replace("×", "*"), mode="eval").body
        if isinstance(tree, (ast.Constant, ast.Name)):
            return None  # "42" or "pi" alone is not a calculation
        value = _eval(tree)
    except (SyntaxError, ValueError, TypeError, ArithmeticError, RecursionError):
        return None
    if isinstance(value, complex):
        return None
    return str(value) if isinstance(value, int) else format(value, ".12g")


# --- apps --------------------------------------------------------------------

_WORD = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def _haystack(app: Any) -> str:
    return " ".join(filter(None, (app.display_name or app.name, app.generic_name, app.executable))).lower()


def fuzzy(query: str, name: str) -> int | None:
    """How well query abbreviates name (lower is better) or None.

    0: initials of the words ("vsc" -> Visual Studio Code, "loc" -> LibreOffice Calc);
    otherwise the span of letters found in order ("chrmium" -> Chromium), capped so that
    a web query like "hello" doesn't match "Hardware Locality lstopo"; spans starting a word win.
    """
    query = query.replace(" ", "")
    initials = iter("".join(word[0] for word in _WORD.findall(name)).lower())
    if all(char in initials for char in query):
        return 0
    lowered, best = name.lower(), None
    word_starts = {match.start() for match in _WORD.finditer(name)}
    for first in (i for i, char in enumerate(lowered) if char == query[0]):
        last = first
        for char in query[1:]:
            last = lowered.find(char, last + 1)
            if last < 0:
                break
        else:
            spread = last - first + 1
            # a run starting mid-word ("tg" in "Godot Engine") ranks below one starting a word (Telegram)
            score = spread + (0 if first in word_starts else 3 * len(query))
            if spread <= 3 * len(query) and (best is None or score < best):
                best = score
    return best


def rank(query: str, apps: list[Any], usage: dict[str, int] | None = None) -> list[Any]:
    """Apps matching query: name prefix, name substring, generic name/executable, then fuzzy; most launched first within a tier."""
    query = query.strip().lower()
    if not query:
        return []
    usage = usage or {}
    hits = []
    for app in apps:
        display = app.display_name or app.name or ""
        name, score = display.lower(), 0
        if name.startswith(query):
            tier = 0
        elif query in name:
            tier = 1
        elif query in _haystack(app):
            tier = 2
        elif len(query) > 1 and (score := fuzzy(query, display)) is not None:
            tier = 3
        else:
            continue
        # fuzzy: initials first, then what you launch most (so "tg" learns Telegram), then the tightest match
        hits.append((tier, score > 0, -usage.get(app.name, 0), score, name, app))
    return [hit[-1] for hit in sorted(hits, key=lambda hit: hit[:5])][:MAX_RESULTS]


def load_usage() -> dict[str, int]:
    try:
        return {str(k): int(v) for k, v in json.loads(USAGE_FILE.read_text()).items()}
    except (OSError, ValueError, AttributeError):
        return {}


# --- sites and links -----------------------------------------------------------

_URL = re.compile(r"(https?://\S+|(?:[\w-]+\.)+[a-z]{2,}(?::\d+)?(?:/\S*)?|localhost(?::\d+)?(?:/\S*)?)", re.IGNORECASE)


def as_url(query: str) -> str | None:
    """query as an openable URL ("youtube.com" -> "https://youtube.com") or None."""
    query = query.strip()
    if not _URL.fullmatch(query):
        return None
    return query if query.lower().startswith(("http://", "https://")) else f"https://{query}"


@dataclass
class Site:
    """A bookmarked site (url) or ssh host (ssh) that ranks and launches like a DesktopApp."""

    name: str
    url: str = ""
    icon: str = "\U000F059F"  # nf-md-web
    ssh: str = ""  # ssh arguments: "root@host" or "user@host -p 2122"

    @property
    def display_name(self) -> str:
        return self.name

    @property
    def generic_name(self) -> str:
        # "ssh" lists every host, "sj24" finds Grafana and Admin
        return f"ssh {self.ssh}" if self.ssh else urlparse(self.url).netloc

    @property
    def detail(self) -> str:
        return self.ssh

    executable = None

    def launch(self) -> None:
        if self.ssh:
            # key auth only; the terminal stays on ssh's error if the host is down
            run(TERMINAL, "-t", f"ssh {self.name}", "-e", "sh", "-c", f'ssh {self.ssh} || read -r _')
        else:
            run(BROWSER, self.url)


@dataclass
class MockApp:
    display_name: str
    name: str
    generic_name: str
    executable: str
    icon: str

    def get_icon_pixbuf(self, *_: Any) -> str:
        return self.icon

    def launch(self) -> None:
        return None


SSH_ICON = "\U000F048B"  # nf-md-server


def parse_sites(text: str) -> list[Site]:
    sites = []
    for name, entry in tomllib.loads(text).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("ssh"):
            sites.append(Site(name, icon=str(entry.get("icon") or SSH_ICON), ssh=str(entry["ssh"])))
        elif entry.get("url"):
            sites.append(Site(name, str(entry["url"]), str(entry.get("icon") or Site.icon)))
    return sites


def load_sites() -> list[Site]:
    try:
        return parse_sites(SITES_FILE.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        print(f"launcher sites: {error}", file=sys.stderr)
        return []


# --- power -------------------------------------------------------------------

POWER = (
    ("lock", "\U000F033E", (str(ROOT / "lock.sh"),), False),  # D-Bus call back into the shell: runs after the launcher hid
    ("suspend", "\U000F0904", ("loginctl", "suspend"), False),
    ("reboot", "\U000F0709", ("loginctl", "reboot"), True),
    ("shutdown", "\U000F0425", ("loginctl", "poweroff"), True),
    ("logout", "\U000F0343", ("i3-msg", "exit"), True),
)


def power(query: str) -> list[Item]:
    query = query.strip().lower()
    if len(query) < 2:
        return []
    return [Item(icon, name.capitalize(), lambda cmd=cmd: run(*cmd), confirm)
            for name, icon, cmd, confirm in POWER if name.startswith(query)]


# --- commands: "> cmd" -------------------------------------------------------

def commands(command: str) -> list[Item]:
    command = command.strip()
    if not command:
        return []
    return [
        # exec $SHELL keeps the terminal open after the command exits
        Item("\U000F018D", f"Run in terminal: {command}", lambda: run(TERMINAL, "-e", "sh", "-c", f'{command}; exec "${{SHELL:-sh}}"')),
        Item("\U000F070E", f"Run in background: {command}", lambda: run("sh", "-c", command)),
    ]


# --- clipboard: "c: text" ----------------------------------------------------

def read_clipboard_history() -> list[str]:
    script = 'var r = []; for (var i = 0; i < size(); i++) r.push(str(read(i)).slice(0, 300)); print(r.join("\\x1e"))'
    try:
        out = subprocess.run(["copyq", "eval", "--", script], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"copyq: {error}", file=sys.stderr)
        return []
    return out.split("\x1e") if out else []


def clipboard(query: str, history: list[str]) -> list[Item]:
    query = query.strip().lower()
    items = []
    for index, entry in enumerate(history):
        label = " ".join(entry.split()) or "[image]"
        if query in label.lower():
            items.append(Item("\U000F0192", label, lambda i=index: run("copyq", "select", str(i))))
        if len(items) == MAX_RESULTS:
            break
    return items


# --- emoji: ":name" ----------------------------------------------------------

_EMOJI_RANGES = ((0x2600, 0x27BF), (0x1F300, 0x1F64F), (0x1F680, 0x1F6FF), (0x1F900, 0x1F9FF), (0x1FA70, 0x1FAFF))


@cache
def _emoji() -> list[tuple[str, str]]:
    return [(chr(code), name.lower()) for start, end in _EMOJI_RANGES for code in range(start, end + 1)
            if (name := unicodedata.name(chr(code), ""))]


def emoji(query: str) -> list[tuple[str, str]]:
    query = query.strip().lower()
    if not query:
        return []
    hits = [(not name.startswith(query), len(name), char, name) for char, name in _emoji() if query in name]
    return [(char, name) for *_, char, name in sorted(hits)[:MAX_RESULTS]]


# --- dispatcher --------------------------------------------------------------

class Sources:
    """Routes a query to its source: ">" commands, "c:" clipboard, ":" emoji, else apps/calc/power/web."""

    def __init__(self) -> None:
        self.apps: list[Any] = []
        self.usage: dict[str, int] = load_usage()
        self._history: list[str] | None = None

    def reset(self, apps: list[Any]) -> None:
        if mock.ENABLED:
            apps = [
                MockApp("Visual Studio Code", "Visual Studio Code", "Code Editor", "code", "󰨞"),
                MockApp("Chromium", "Chromium", "Web Browser", "chromium", "󰊯"),
                MockApp("Files", "Files", "File Manager", "nautilus", "󰉋"),
            ]
            self.apps = apps + [Site("Build server", ssh="dev@build.example"), Site("Production", ssh="ops@prod.example")]
        else:
            self.apps = apps + load_sites()
        self._history = None  # re-read lazily, only if "c:" is typed during this open

    def search(self, query: str, app_icon: Callable[[Any], Any], web_icon: Any) -> list[Item]:
        stripped = query.strip()
        if not stripped:
            return []
        if stripped.startswith(">"):
            return commands(stripped[1:])
        if stripped.lower().startswith("c:"):
            if self._history is None:
                self._history = read_clipboard_history()
            return clipboard(stripped[2:], self._history)
        if stripped.startswith(":"):
            return [Item(char, name, lambda char=char: copy_text(char)) for char, name in emoji(stripped[1:])]
        items = []
        if (url := as_url(stripped)) is not None:
            items.append(Item(web_icon, f"Open {url}", lambda: run(BROWSER, url)))
        if (result := calc(stripped)) is not None:
            items.append(Item("\U000F00EC", f"= {result}", lambda: copy_text(result)))
        items += power(stripped)  # before apps: a typed power keyword is deliberate, and it asks to confirm
        apps = rank(stripped, self.apps, self.usage)
        exact = bool(items) or any(stripped.lower() in _haystack(app) for app in apps)
        items += [Item(app.icon, app.name, lambda app=app: self.launch(app), detail=app.detail) if isinstance(app, Site)
                  else Item(app_icon(app), app.display_name or app.name, lambda app=app: self.launch(app)) for app in apps]
        if exact:
            return items
        # only fuzzy guesses (or nothing): the query is probably meant for the web
        return items[:MAX_RESULTS - 1] + [Item(web_icon, f"Search “{stripped}” in Chromium", lambda: run(BROWSER, SEARCH_URL + quote_plus(stripped)))]

    def launch(self, app: Any) -> None:
        self.usage[app.name] = self.usage.get(app.name, 0) + 1
        try:
            USAGE_FILE.write_text(json.dumps(self.usage))
        except OSError as error:
            print(f"launcher usage: {error}", file=sys.stderr)
        app.launch()
