"""X11 monitor discovery via xrandr."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Monitor:
    name: str
    width: int
    height: int
    x: int
    y: int


def parse_monitors(value: str) -> list[Monitor]:
    monitors = []
    for line in value.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 3:
            continue
        match = re.fullmatch(r"(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)", fields[2])
        if match:
            monitors.append(
                Monitor(fields[1].lstrip("+*"), *(int(part) for part in match.groups()))
            )
    return monitors


def read_monitors() -> list[Monitor]:
    output = subprocess.check_output(
        ["xrandr", "--listactivemonitors"], text=True, stderr=subprocess.DEVNULL
    )
    return parse_monitors(output)
