"""Paths and shared layout metrics."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
RUNTIME = Path.home() / ".cache" / "fabric-shell"
RUNTIME.mkdir(parents=True, exist_ok=True)
DND_FILE = RUNTIME / "dnd"

I3_GAP = 15
POPUP_INSET = 10  # popups sit this far inside the tiled-window area instead of on its edge
CONTENT_GAP = I3_GAP * 2 + POPUP_INSET
BAR_TOP = 5
BAR_HEIGHT = 40
POPUP_TOP = BAR_TOP + BAR_HEIGHT + I3_GAP + POPUP_INSET

GIB = 1073741824
HOT = 80  # °C: a temperature past this is shown as an alert (bar, system monitor)
