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

from modules import dayline as dayline_module
from modules import display as display_module
from modules import bar as bar_module
from modules import calendar as calendar_module
from modules import claude as claude_module
from modules import launcher as launcher_module
from modules import lock as lock_module
from modules import music as music_module
from modules import network as network_module
from modules import notifications as notifications_module
from modules import auth as auth_module
from modules import sound as sound_module
from modules import sysmon as sysmon_module
from modules import volume_osd as volume_osd_module
from modules import voice as voice_module
from modules.bar import Bar
from modules.calendar import CalendarWindow
from modules.launcher import LauncherWindow
from services.launcher import as_url, parse_sites, calc, clipboard, commands, emoji, fuzzy, power, rank
from modules.music import MusicWindow, local_art_path
from modules.notifications import NotificationHub
from modules.registry import ModuleRegistry, ModuleSpec
from modules.volume_osd import VolumeOSD
from services.monitors import Monitor, parse_monitors, read_monitors
from services.state import JsonState, State, parse_json
from services.system import BacklightState, ClockState, KeyboardState, NetworkState, SystemState, busy, cpu_model, default_interface, human_bytes
from shared.constants import ROOT, SCRIPTS
from shared.widgets import volume_icon


class Shell:
    def __init__(self):
        self.monitors = read_monitors()
        if not self.monitors:
            raise RuntimeError("No active X11 monitors found")
        self.clock = ClockState()
        self.system = SystemState()
        self.workspaces = JsonState(SCRIPTS / "workspaces.sh", [])
        self.audio = JsonState(SCRIPTS / "audio.sh", {"vol": 0, "muted": False})
        self.network = NetworkState()
        self.backlight = BacklightState()
        self.music = JsonState(SCRIPTS / "music.sh", {"status": "Stopped", "title": "No media player", "artist": "", "position": 0, "length": 1, "elapsed": "0:00", "duration": "0:00"}, autostart=False)
        self.voice_state = JsonState(SCRIPTS / "voice.py", {"state": "loading"}, autostart=False)
        self.keyboard = KeyboardState()
        self.registry = ModuleRegistry(
            (
                ModuleSpec("calendar", calendar_module.build),
                ModuleSpec("sysmon", sysmon_module.build),
                ModuleSpec("sound", sound_module.build),
                ModuleSpec("network", network_module.build),
                ModuleSpec("claude", claude_module.build),
                ModuleSpec("display", display_module.build),
                ModuleSpec("bar", bar_module.build),
                ModuleSpec("music", music_module.build),
                ModuleSpec("volume_osd", volume_osd_module.build),
                ModuleSpec("notifications", notifications_module.build),
                ModuleSpec("launcher", launcher_module.build),
                ModuleSpec("dayline", dayline_module.build),
                ModuleSpec("voice", voice_module.build),
                ModuleSpec("lock", lock_module.build),
                ModuleSpec("auth", auth_module.build),
            )
        )
        self.calendars: list[CalendarWindow] = []
        self.sysmons: list[sysmon_module.SystemMonitorWindow] = []
        self.sounds: list[sound_module.SoundWindow] = []
        self.network_panels: list[network_module.NetworkWindow] = []
        self.claude_panels: list[claude_module.ClaudeWindow] = []
        self.displays: list[display_module.DisplayWindow] = []
        self.bars: list[Bar] = []
        self.music_window: MusicWindow | None = None
        self.volume_osd: VolumeOSD | None = None
        self.notifications: NotificationHub | None = None
        self.launcher: LauncherWindow | None = None
        self.dayline: dayline_module.DaylineWindow | None = None
        self.voice: voice_module.VoiceWindow | None = None
        self.lock: lock_module.Lock | None = None
        self.windows = self.registry.build(self)


shell: Shell | None = None


@Application.action("toggle-music")
def toggle_music() -> None:
    if shell:
        shell.music_window.toggle()


@Application.action("toggle-notifications")
def toggle_notifications() -> None:
    if shell:
        shell.notifications.toggle_center()


@Application.action("toggle-launcher")
def toggle_launcher() -> None:
    if shell:
        shell.launcher.toggle()


@Application.action("toggle-dayline")
def toggle_dayline() -> None:
    if shell:
        shell.dayline.toggle()


@Application.action("toggle-voice")
def toggle_voice() -> None:
    if shell:
        shell.voice.toggle()


@Application.action("lock")
def lock() -> None:
    if shell:
        shell.lock.lock()


@Application.action("show-volume-osd")
def show_volume_osd() -> None:
    if shell:
        shell.volume_osd.open_temporarily()


def self_check() -> None:
    sample = "Monitors: 2\n 0: +*HDMI-1-0 1920/600x1080/332+1920+0 HDMI-1-0\n 1: +eDP-1 1920/344x1080/193+0+0 eDP-1\n"
    assert parse_monitors(sample) == [
        Monitor("HDMI-1-0", 1920, 1080, 1920, 0),
        Monitor("eDP-1", 1920, 1080, 0, 0),
    ]
    assert parse_json('{"ok": true}', {}) == {"ok": True}
    assert parse_json("broken", {"ok": False}) == {"ok": False}
    assert local_art_path("https://example.com/cover.png") is None
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
    sinks = [
        {"name": "hs", "description": "G435 Wireless Gaming Headset Digital Stereo (IEC958)", "mute": False,
         "volume": {"front-left": {"value_percent": "83%"}, "front-right": {"value_percent": "80%"}}, "properties": {"device.form_factor": "headset"}},
        {"name": "hs.monitor", "description": "Monitor of G435", "mute": False, "volume": {}, "properties": {"device.class": "monitor"}},
        {"name": "pci", "description": "Built-in Audio Analog Stereo", "mute": True, "volume": {"mono": {"value_percent": "5%"}}},
    ]
    assert sound_module.devices(sinks, "pci") == [
        {"name": "hs", "label": "G435 Wireless Gaming Headset", "kind": "headset", "volume": 83, "muted": False, "default": False},
        {"name": "pci", "label": "Built-in Audio", "kind": "", "volume": 5, "muted": True, "default": True},
    ]
    wifi = "*:79:WPA1 WPA2:Home\n :34:WPA2:Home\n :90:--:Cafe\\: Free\n :50::\n :20:WPA2:Work\n :60:WPA2:Home 5G\n"
    assert [(n["ssid"], n["signal"], n["secure"], n["active"], n["known"]) for n in network_module.networks(wifi, {"Home", "Work"})] == [
        ("Home", 79, True, True, True), ("Work", 20, True, False, True), ("Cafe: Free", 90, False, False, False), ("Home 5G", 60, True, False, False)]
    assert network_module.saved("a1:Home\nb2:Cafe: Free\nc3:Home\nd4:\n") == {"Home": ["a1", "c3"], "Cafe: Free": ["b2"]}
    devices = "wlan0:wifi:connected:Home\n9C\\:92:bt:disconnected:\neth0:ethernet:unavailable:\n"
    assert network_module.links(devices, "ethernet") == [{"device": "eth0", "state": "unavailable", "connection": ""}]
    assert [network_module.signal_icon(s) for s in (10, 40, 70, 95)] == ["󰤟", "󰤢", "󰤥", "󰤨"]
    assert claude_module.current({"pct": 40, "resets": 1000}, 400) == (40, 600) and claude_module.current({"pct": 40, "resets": 1000}, 1000) == (0, 0)
    assert [claude_module.duration(s) for s in (1, 2700, 8040, 108000)] == ["1m", "45m", "2h 14m", "1d 6h"]
    assert [claude_module.pace(p, r, 100) for p, r in ((50, 50), (52, 50), (60, 50), (38, 50))] == ["on pace", "on pace", "10% ahead of pace", "12% under pace"]
    assert [claude_module.level({"severity": s}, p) for s, p in (("normal", 36), ("warning", 80), ("warning", 0), ("normal", 100), ("critical", 95))] == ["", "warn", "", "alert", "alert"]
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
    print("config self-check: ok")


def main() -> None:
    global shell
    shell = Shell()
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
