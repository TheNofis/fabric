"""Module: the contract every feature of the shell follows."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class Module(ABC):
    """One feature of the shell (a folder in modules/). config.py builds MODULES in order.

    build() makes the windows; the shell keeps them in `windows`. Other modules reach this
    one through `shell.modules[name]`, the shared streams (clock, audio, ...) are on the shell.
    """

    name: ClassVar[str]
    action: ClassVar[str | None] = None  # D-Bus action (the toggle-*.sh scripts), runs activate()

    def __init__(self, shell: Any):
        self.shell = shell
        self.windows: list[Any] = []

    @abstractmethod
    def build(self) -> list[Any]:
        """The module's windows, built once at startup."""

    def activate(self) -> None:
        self.windows[0].toggle()

    @staticmethod
    def check() -> None:
        """Self-check of the module's plain logic, no display needed; `config.py --check` runs every one."""
