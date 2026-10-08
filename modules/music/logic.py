"""Music without GTK: track time and the local cover file."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse



def clock(us: float) -> str:
    seconds = int(us // 1_000_000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def local_art_path(value: str) -> str | None:
    if value.startswith("file://"):
        value = unquote(urlparse(value).path)
    if value.startswith("/") and Path(value).is_file():
        return bluez_cover(value)
    return None


def bluez_cover(path: str) -> str:
    """bluez pulls the iPhone's cover twice per track; the second pull is iOS's grey placeholder.
    Take the real one: the previous /tmp/sessionN-M file, fetched within the same second."""
    match = re.fullmatch(r"(/tmp/session\d+-)(\d+)", path)
    if not match:
        return path
    first = f"{match[1]}{int(match[2]) - 1}"
    try:
        return first if os.stat(path).st_mtime - os.stat(first).st_mtime < 1 else path
    except OSError:
        return path


def check() -> None:
    assert local_art_path("https://example.com/cover.png") is None
    assert [clock(0), clock(61_500_000), clock(3_600_000_000)] == ["0:00", "1:01", "60:00"]
    assert bluez_cover("/home/cover.png") == "/home/cover.png"
