"""Small, explicit module registry inspired by Tsumiki's module layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


WindowFactory = Callable[[Any], Iterable[Any]]


@dataclass(frozen=True)
class ModuleSpec:
    """A named module and the windows it contributes to the shell."""

    name: str
    build: WindowFactory


class ModuleRegistry:
    """Compose enabled desktop modules without coupling them to the app loop."""

    def __init__(self, specs: Iterable[ModuleSpec] = ()):
        self._specs: dict[str, ModuleSpec] = {spec.name: spec for spec in specs}

    def register(self, spec: ModuleSpec) -> None:
        self._specs[spec.name] = spec

    def build(self, context: Any, enabled: Iterable[str] | None = None) -> list[Any]:
        names = set(enabled) if enabled is not None else set(self._specs)
        windows: list[Any] = []
        for name, spec in self._specs.items():
            if name in names:
                windows.extend(spec.build(context))
        return windows

    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)
