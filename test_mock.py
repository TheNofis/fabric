"""Run with FABRIC_MOCK=1 python test_mock.py in the shell's virtualenv."""

from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import config  # loads the GTK versions used by the shell
from services import mock
from modules.dayline.store import Note, Notes
from services.launcher import Sources
from services.state import JsonState
from services.system import BacklightState, ClockState, KeyboardState, NetworkState, SystemState
from shared.widgets import copy_text, run

assert mock.ENABLED, "Run with FABRIC_MOCK=1"
with patch("gi.repository.GLib.spawn_async", side_effect=AssertionError("system command")), patch(
    "gi.repository.Gio.Subprocess.new", side_effect=AssertionError("script or nmcli")
), patch("gi.repository.Gtk.Clipboard.get", side_effect=AssertionError("real clipboard")):
    for script in ("audio.sh", "workspaces.sh", "music.sh", "gpu.sh", "sound.sh", "network.sh", "claude.py", "voice.py"):
        stream = JsonState(Path(script), {})
        assert stream.value and stream.process is None
        stream.stop()
        stream.start()
        assert stream.value == mock.json_for(script)
    assert ClockState().value == mock.NOW
    assert SystemState().value == mock.system()
    assert NetworkState().value == mock.network()
    assert KeyboardState().value == {"layout": "us", "caps": False}
    light = BacklightState()
    light.set(500)
    assert light.value == 100
    from services.display import DisplayState
    display = DisplayState(light, Path("/nonexistent/display.json"))
    display.store("Demo")
    display.apply("Night")
    assert light.value == 25 and display.value["active"] == "Night"
    run("systemctl", "poweroff")
    copy_text("demo")
    config.network_module.nmcli(["radio", "wifi", "off"], lambda ok, error: None)
    sources = Sources()
    sources.reset([])
    assert sources.search("ssh", lambda app: app.icon, "web")[0].detail == "dev@build.example"
    assert sources.search("c:", lambda app: app.icon, "web")[0].label == "git status --short"
    with TemporaryDirectory() as directory:
        path = Path(directory) / "notes.json"
        notes = Notes(path)
        notes.add(mock.NOW.date(), "14:00", "Demo edit", now=mock.NOW)
        assert not path.exists() and any(note.text == "Demo edit" for note in notes.value)
now = mock.NOW.replace(hour=12, minute=0)
today = now.date().isoformat()
yesterday = (now.date() - timedelta(days=1)).isoformat()
assert Note(1, yesterday, "", "a").overdue(now) and Note(1, today, "11:00", "a").overdue(now)
assert not any(n.overdue(now) for n in (Note(1, today, "", "a"), Note(1, today, "13:00", "a"), Note(1, yesterday, "", "a", done=today), Note(1, "", "", "a")))
JsonState.stop_all()
print("mock isolation: ok")
