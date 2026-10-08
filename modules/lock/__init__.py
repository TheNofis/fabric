"""Lock screen: a cover on every monitor, the password (PAM) on the first one."""

from __future__ import annotations

from typing import Any

from modules.base import Module
from modules.lock.window import Lock as LockScreen


class Lock(Module):
    name = "lock"
    action = "lock"

    def build(self) -> list[Any]:
        self.screen = LockScreen(self.shell)
        return self.screen.windows

    @property
    def locked(self) -> bool:
        return self.screen.locked

    def activate(self) -> None:
        self.screen.lock()
