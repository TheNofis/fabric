#!/usr/bin/env python
"""Entrypoint: builds the Shell context, registers modules and runs Fabric."""
from __future__ import annotations

import signal
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GLib

from fabric import Application

from modules.auth import Auth
from modules.bar import Bar
from modules.base import Module
from modules.calendar import Calendar
from modules.claude import Claude
from modules.dayline import Dayline
from modules.display import Display
from modules.launcher import Launcher
from modules.lock import Lock
from modules.messages import Messages
from modules.music import Music
from modules.network import Network
from modules.notifications import Notifications
from modules.sound import Sound
from modules.sysmon import Sysmon
from modules.voice import Voice
from modules.volume_osd import VolumeOsd
from services.launcher import as_url, parse_sites, calc, clipboard, commands, emoji, fuzzy, power, rank
from services.monitors import Monitor, parse_monitors, read_monitors
from services.state import JsonState, State, parse_json
from services.system import BacklightState, ClockState, KeyboardState, NetworkState, SystemState, busy, cpu_model, default_interface, human_bytes
from shared.constants import ROOT, SCRIPTS
from shared.widgets import volume_icon


# The desktop, built in this order: the bar after the panels it opens, Messages after
# Notifications (it takes over their Reply). A new module is a folder and a line here.
MODULES: list[type[Module]] = [
    Calendar, Sysmon, Sound, Network, Claude, Display, Bar, Music, VolumeOsd,
    Notifications, Launcher, Dayline, Messages, Voice, Lock, Auth,
]


class Shell:
    def __init__(self):
        self.monitors = read_monitors()
        if not self.monitors:
            raise RuntimeError("No active X11 monitors found")
        self.clock = ClockState()
        self.system = SystemState()
        self.audio = JsonState(SCRIPTS / "audio.sh", {"vol": 0, "muted": False})
        self.network = NetworkState()
        self.backlight = BacklightState()
        self.voice_state = JsonState(SCRIPTS / "voice.py", {"state": "loading"}, autostart=False)
        self.keyboard = KeyboardState()
        self.modules: dict[str, Module] = {}
        self.windows: list[Any] = []
        for cls in MODULES:
            module = self.modules[cls.name] = cls(self)
            module.windows = module.build()
            self.windows += module.windows



def self_check() -> None:
    sample = "Monitors: 2\n 0: +*HDMI-1-0 1920/600x1080/332+1920+0 HDMI-1-0\n 1: +eDP-1 1920/344x1080/193+0+0 eDP-1\n"
    assert parse_monitors(sample) == [
        Monitor("HDMI-1-0", 1920, 1080, 1920, 0),
        Monitor("eDP-1", 1920, 1080, 0, 0),
    ]
    assert parse_json('{"ok": true}', {}) == {"ok": True}
    assert parse_json("broken", {"ok": False}) == {"ok": False}
    routes = (
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
        "wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\n"
        "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\n"
        "docker0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\n"
    )
    assert default_interface(routes) == "eth0"
    assert default_interface(routes.splitlines()[0]) == ""
    assert busy((200, 50), (100, 25)) == 75.0 and busy((100, 25), (100, 25)) == 0.0
    assert cpu_model("processor\t: 0\nmodel name\t: Intel(R) Core(TM) i5-7300HQ CPU @ 2.50GHz\n") == "Intel Core i5-7300HQ"
    assert cpu_model("model name\t: AMD Ryzen 7 5800X 8-Core Processor\n") == "AMD Ryzen 7 5800X 8-Core" and cpu_model("") == ""
    assert [human_bytes(v) for v in (0, 999, 1000, 5 * 1024, 20 * 1024, 3 * 1024**3)] == ["0B", "999B", "1.0K", "5.0K", "20K", "3.0G"]
    seen: list[Any] = []
    state = State(1)
    state.subscribe(seen.append)
    state.emit(1)
    state.emit(2)
    assert seen == [1, 2]
    assert [volume_icon(v, m) for v, m in ((50, True), (10, False), (50, False), (90, False))] == ["󰖁", "󰕿", "󰖀", "󰕾"]
    App = type("App", (), {})
    apps = [App(), App(), App()]
    for app, (name, generic, exe) in zip(apps, (("Firefox", "Web Browser", "firefox"), ("Kitty", "Terminal", "kitty"), ("Nautilus", "Files", "nautilus"))):
        app.display_name, app.name, app.generic_name, app.executable = name, name, generic, exe
    assert rank("", apps) == []
    assert rank("fi", apps) == [apps[0], apps[2]]  # name prefix before generic-name match
    assert rank("TERM", apps) == [apps[1]]
    assert [calc(q) for q in ("2+2*3", "2^10", "10/4", "sqrt(16)", "-3", "(1+2)*pi")] == ["8", "1024", "2.5", "4", "-3", "9.42477796077"]
    assert [calc(q) for q in ("42", "pi", "firefox", "9**9**9", "1/0", "sqrt(-1)", "__import__('os')", "(-8)**0.5")] == [None] * 8
    assert rank("fi", apps, {"Nautilus": 5}) == [apps[0], apps[2]]  # prefix beats usage
    apps[1].display_name = apps[1].name = "Files Kitty"
    assert rank("fi", apps, {"Files Kitty": 3}) == [apps[1], apps[0], apps[2]]  # usage breaks prefix ties
    assert [i.label for i in power("lo")] == ["Lock", "Logout"] and not power("lo")[0].confirm
    assert [i.label for i in power("re")] == ["Reboot"] and power("re")[0].confirm and power("r") == []
    assert [i.label for i in commands(" htop ")] == ["Run in terminal: htop", "Run in background: htop"] and commands(" ") == []
    assert [i.label for i in clipboard("FO", ["foo\nbar", "baz", ""])] == ["foo bar"]
    assert clipboard("", ["", "x"])[0].label == "[image]"
    assert emoji("fire")[0] == ("\U0001F525", "fire") and emoji("") == []
    assert fuzzy("vsc", "Visual Studio Code") == 0 and fuzzy("loc", "LibreOffice Calc") == 0
    assert fuzzy("chrmium", "Chromium") == 8 and fuzzy("hello", "Hardware Locality lstopo") is None
    assert fuzzy("xyz", "Chromium") is None
    assert fuzzy("tg", "Telegram") < fuzzy("tg", "Godot Engine")  # word start beats a tighter mid-word run
    vsc, chromium = App(), App()
    vsc.display_name = vsc.name = "Visual Studio Code"
    chromium.display_name = chromium.name = "Chromium"
    for app in (vsc, chromium):
        app.generic_name, app.executable = None, None
    assert rank("vsc", [chromium, vsc]) == [vsc]
    assert rank("chrmium", [chromium, vsc]) == [chromium]
    telegram, dst = App(), App()
    telegram.display_name = telegram.name = "Telegram"
    dst.display_name = dst.name = "Don't Starve Together"
    for app in (telegram, dst):
        app.generic_name, app.executable = None, None
    assert rank("tg", [telegram, dst]) == [dst, telegram] and rank("tg", [telegram, dst], {"Telegram": 1}) == [telegram, dst]
    assert rank("co", [vsc, chromium]) == [vsc, chromium]  # substring ("code") before fuzzy (c..o)
    assert [as_url(q) for q in ("https://youtube.com/watch?v=1", "youtube.com", "grafana.sj24.ru/dashboards", "localhost:3000")] == [
        "https://youtube.com/watch?v=1", "https://youtube.com", "https://grafana.sj24.ru/dashboards", "https://localhost:3000"]
    assert [as_url(q) for q in ("2.5", "hello world", "chromium", "a.b c")] == [None] * 4
    sites = parse_sites('[GitHub]\nurl = "https://github.com"\n[Grafana]\nurl = "https://grafana.sj24.ru/dashboards"\nicon = "G"\n[Broken]\nicon = "x"\n')
    assert [(site.name, site.icon) for site in sites] == [("GitHub", "\U000F059F"), ("Grafana", "G")]
    assert rank("sj24", sites) == [sites[1]] and rank("gh", sites) == [sites[0]]
    from services.launcher import SITES_FILE
    assert len(parse_sites(SITES_FILE.read_text())) == 8
    host = parse_sites('[box]\nssh = "u@h -p 22"\n')[0]
    assert (host.ssh, host.detail, host.generic_name) == ("u@h -p 22", "u@h -p 22", "ssh u@h -p 22") and rank("ssh", [host]) == [host]
    from services.polkit import pick_identity
    root, me, wheel = ("unix-user", {"uid": 0}), ("unix-user", {"uid": 1000}), ("unix-group", {"gid": 998})
    assert pick_identity([root, wheel, me], 1000) == me and pick_identity([wheel, root], 1000) == root and pick_identity([wheel], 1000) == wheel
    from services.display import DisplayState, ramp, whitepoint
    assert whitepoint(6500) == (1.0, 1.0, 1.0) and whitepoint(3400)[2] < whitepoint(3400)[1] < 1.0
    red, green, blue = ramp(256, 100, 100, 6500)
    assert red == green == blue and red[0] == 0 and red[-1] == 65535 and red[128] == round(128 / 255 * 65535)
    assert ramp(3, 50, 100, 6500)[0] == [16384, 32768, 49151] and ramp(3, 150, 100, 6500)[0] == [0, 32768, 65535]
    assert ramp(3, 100, 200, 6500)[0][1] == round(0.5 ** 0.5 * 65535)
    with TemporaryDirectory() as directory, patch("services.display.set_gamma") as set_gamma:  # never touch the real screen
        light = State(60)
        light.set = light.emit
        store = DisplayState(light, Path(directory) / "display.json")
        store.adjust(warmth=4000, contrast=999)
        assert store.value["settings"] == {"contrast": 150, "gamma": 100, "warmth": 4000}
        store.store(" Mine ")
        assert store.value["active"] == "Mine" and store.preset("Mine")["brightness"] == 60 and not store.modified()
        light.emit(30)
        assert store.modified()
        store.apply("Mine")
        assert light.value == 60 and not store.modified()
        store.delete("Mine")
        assert store.value["active"] == "" and store.preset("Mine") is None
        assert DisplayState(light, Path(directory) / "display.json").value == store.value  # reloaded from disk
        assert set_gamma.call_args.kwargs == {"contrast": 150, "gamma": 100, "warmth": 4000}
    assert {cls.action for cls in MODULES} - {None} == {  # the toggle-*.sh scripts and i3 call these by name
        "toggle-music", "toggle-notifications", "toggle-launcher", "toggle-dayline", "toggle-messages", "toggle-voice", "lock", "show-volume-osd"}
    for cls in MODULES:
        cls.check()
    print("config self-check: ok")


def main() -> None:
    shell = Shell()
    for module in shell.modules.values():
        if module.action:
            Application.action(module.action)(module.activate)
    app = Application("fabric-shell", *shell.windows)
    app.set_stylesheet_from_file(str(ROOT / "style.css"), compile=False)
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, app.quit)
    try:
        app.run()
    finally:
        JsonState.stop_all()


if __name__ == "__main__":
    self_check() if "--check" in sys.argv else main()
