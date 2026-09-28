"""UI modules for the Fabric shell.

Each module is intentionally small: it receives the shared shell context and
returns one or more windows.  The registry is the only place where the
composition of the desktop is defined.
"""

from .registry import ModuleRegistry, ModuleSpec

__all__ = ["ModuleRegistry", "ModuleSpec"]
