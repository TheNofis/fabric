"""Network panel: Wi-Fi networks and wired links, dropped from the bar's network slot."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from gi.repository import Gio, Gtk, Pango

from services.monitors import Monitor
from services.state import JsonState, State
from services import mock
from shared.constants import SCRIPTS
from shared.ui import big_value, button, check, header, list_row, panel, password_entry, password_field, row_list, switch
from shared.widgets import flag, text
from shared.window import BarPanel

MAX_NETWORKS = 8  # ponytail: strongest N only; a scrolled list if a crowded place needs more
FIELD = re.compile(r"(?<!\\):")


def table(raw: str) -> list[list[str]]:
    """`nmcli -t` output -> rows of fields; terse mode escapes ':' and '\\' inside values."""
    return [[field.replace("\\:", ":").replace("\\\\", "\\") for field in FIELD.split(line)] for line in raw.splitlines() if line]


def networks(raw: str, known: set[str]) -> list[dict[str, Any]]:
    """One entry per SSID (the strongest access point), connected first, then saved, then by signal."""
    seen: dict[str, dict[str, Any]] = {}
    for in_use, signal, security, ssid in (row for row in table(raw) if len(row) == 4):
        if not ssid:
            continue  # hidden network
        entry = seen.setdefault(ssid, {"ssid": ssid, "signal": 0, "secure": False, "active": False, "known": ssid in known})
        entry["signal"] = max(entry["signal"], int(signal or 0))
        entry["secure"] |= security not in ("", "--")
        entry["active"] |= in_use == "*"
    return sorted(seen.values(), key=lambda n: (not n["active"], not n["known"], -n["signal"]))[:MAX_NETWORKS]


def saved(raw: str) -> dict[str, list[str]]:
    """`uuid:ssid` lines -> SSID -> profile UUIDs (a network can have several profiles)."""
    profiles: dict[str, list[str]] = {}
    for line in raw.splitlines():
        uuid, _, ssid = line.partition(":")
        if ssid:
            profiles.setdefault(ssid, []).append(uuid)
    return profiles


def links(raw: str, kind: str) -> list[dict[str, str]]:
    return [{"device": row[0], "state": row[2], "connection": row[3]} for row in table(raw) if len(row) == 4 and row[1] == kind]


def signal_icon(signal: int) -> str:
    return "󰤟" if signal < 30 else "󰤢" if signal < 55 else "󰤥" if signal < 80 else "󰤨"


def nmcli(args: list[str], done: Callable[[bool, str], None]) -> None:
    """Run nmcli without blocking the UI; done(ok, stderr)."""
    if mock.ENABLED:
        done(True, "")
        return
    process = Gio.Subprocess.new(["nmcli", "--wait", "20", *args], Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE)
    process.communicate_utf8_async(None, None, lambda proc, result: done(proc.get_successful(), proc.communicate_utf8_finish(result)[2] or ""))


class NetworkWindow(BarPanel):
    def __init__(self, monitor: Monitor, state: JsonState, route: State):
        self.value: dict[str, Any] = {}
        self.route: dict[str, Any] = {}
        self.pending = ""  # SSID or device being connected
        self.asking = ""  # secured SSID waiting for its password
        self.open = ""  # saved SSID with its options (disconnect, forget) shown
        self.arming = ""  # SSID whose Forget was clicked once; the second click deletes it
        self.syncing = False
        self.shown: Any = None
        self.profiles: dict[str, list[str]] = {}

        self.name = big_value()
        self.name.set_ellipsize(Pango.EllipsizeMode.END)
        self.name.set_max_width_chars(18)
        self.detail = text("", "ui-detail", xalign=0)

        self.switch = switch(self.on_radio)
        self.wifi_list = row_list()
        self.entry = password_entry(lambda: self.entry.get_text() and self.join(self.asking, self.entry.get_text()))
        self.password = password_field(self.entry, classes=("net-password",))
        self.hint = text("", "net-hint", xalign=0)
        self.buttons = Box(
            spacing=8,
            style_classes=("net-form",),
            children=[
                Box(h_expand=True),
                button("Cancel", lambda *_: self.ask(""), "net-action"),
                button("Join", lambda *_: self.entry.get_text() and self.join(self.asking, self.entry.get_text()), "net-action", primary=True),
            ],
        )
        self.wifi = Box(
            orientation="v",
            spacing=6,
            children=[
                header("Wi-Fi", self.switch),
                self.wifi_list,
                self.password,
                self.hint,
                self.buttons,
            ],
        )
        self.wired_list = row_list()
        self.wired = Box(orientation="v", spacing=6, children=[header("Ethernet"), self.wired_list])
        for widget in (self.wifi, self.wifi_list, self.password, self.buttons, self.hint, self.wired):
            widget.set_no_show_all(True)
            widget.hide()  # Fabric widgets start visible

        super().__init__(
            monitor,
            "network",
            panel(Box(orientation="v", spacing=2, children=[self.name, self.detail]), self.wifi, self.wired),
        )
        self.connect("show", lambda *_: state.start())
        self.connect("hide", lambda *_: (state.stop(), setattr(self, "open", ""), self.ask("")))
        state.subscribe(self.update)
        route.subscribe(self.on_route)

    # header: the connection carrying the default route, from the bar's own network state
    def on_route(self, value: dict[str, Any]) -> None:
        self.route = value
        iface = str(value.get("iface", ""))
        connection = next((link["connection"] for link in links(self.value.get("devices", ""), "wifi") + links(self.value.get("devices", ""), "ethernet") if link["device"] == iface), "")
        kind = "Wi-Fi" if iface.startswith("w") else "Ethernet"
        self.name.set_text(connection or (kind if iface else "Offline"))
        self.detail.set_text(f"{kind} · {value.get('ip', '')}" if iface else "Not connected")
        flag(self.name, "dim", not iface)

    def update(self, value: dict[str, Any]) -> None:
        if not value:
            return
        self.value = value
        self.on_route(self.route)
        devices = value.get("devices", "")
        radio = value.get("radio") == "enabled"
        self.profiles = saved(value.get("known", ""))
        found = networks(value.get("wifi", ""), set(self.profiles))
        if any(n["ssid"] == self.pending and n["active"] for n in found):
            self.pending = ""

        self.syncing = True
        self.switch.set_active(radio)
        self.syncing = False
        self.wifi_list.set_visible(radio)
        if self.asking:  # only the network being joined; kept even if it drops out of a scan
            found = [n for n in found if n["ssid"] == self.asking] or [{"ssid": self.asking, "signal": 0, "secure": True, "active": False, "known": False}]
        wired = links(devices, "ethernet")
        self.wifi.set_visible(bool(links(devices, "wifi")))
        self.wired.set_visible(bool(wired))

        # rebuild rows only when something visible changed, so a hovered row isn't reset every scan
        shown = (found, wired, self.pending, self.asking, self.open, self.arming)
        if shown != self.shown:
            self.shown = shown
            self.wifi_list.children = [widget for n in found for widget in self.network_rows(n)]
            self.wired_list.children = [self.wired_row(link) for link in wired]
        self.wifi_list.show_all()
        self.wired.show_all()
        if self.wifi.get_visible():
            self.wifi.show_all()
            self.password.set_visible(bool(self.asking))
            self.buttons.set_visible(bool(self.asking))
            self.hint.set_visible(bool(self.hint.get_text()))

    def network_rows(self, n: dict[str, Any]) -> list[Gtk.Widget]:
        ssid = n["ssid"]
        if ssid == self.pending:
            end = text("Connecting…", "net-status")
        elif n["active"]:
            end = check()
        else:
            end = text("󰌾" if n["secure"] else "", "net-lock")
        needs_password = n["secure"] and not n["known"]
        menu = (lambda: self.toggle_options(ssid)) if n["known"] and not self.asking else None
        if menu and not n["active"] and ssid != self.pending:
            more = Button(label="\U000F01D8", style_classes=("net-more",), tooltip_text="Options", on_clicked=lambda *_: menu())  # nf-md-dots_horizontal
            more.set_can_focus(False)
            end = Box(spacing=6, children=[end, more])
        if n["active"]:
            click, tooltip = menu and (lambda *_: menu()), "Options"
        else:
            click = lambda *_: self.ask("" if self.asking == ssid else ssid) if needs_password else self.join(ssid)
            tooltip = "Cancel" if ssid == self.asking else "Join"
        selected = n["active"] or ssid in (self.asking, self.open)
        rows: list[Gtk.Widget] = [list_row(
            signal_icon(n["signal"]), ssid, end, classes=("default",) if selected else (), on_clicked=click, tooltip=tooltip,
            icon_classes=("net-signal", *(("weak",) if n["signal"] < 40 else ())), on_menu=menu,
        )]
        if ssid == self.open:
            armed = self.arming == ssid
            buttons = [button("Forget?" if armed else "Forget", lambda *_: self.forget(ssid), "net-action", *(("armed",) if armed else ()))]
            if n["active"]:
                buttons.append(button("Disconnect", lambda *_: self.disconnect(), "net-action"))
            rows.append(Box(spacing=8, style_classes=("net-options",), children=[Box(h_expand=True), *buttons]))
        return rows

    def toggle_options(self, ssid: str) -> None:
        self.open = "" if self.open == ssid else ssid
        self.arming = ""
        self.update(self.value)

    def disconnect(self) -> None:
        self.open = ""
        for link in links(self.value.get("devices", ""), "wifi"):
            if link["state"] == "connected":
                self.run(link["device"], ["device", "disconnect", link["device"]], "Couldn't disconnect")

    def forget(self, ssid: str) -> None:
        if self.arming != ssid:  # deletes the profile and its password: confirm with a second click
            self.arming = ssid
            self.update(self.value)
            return
        self.open = ""
        self.run("", ["connection", "delete", *(arg for uuid in self.profiles.get(ssid, []) for arg in ("uuid", uuid))], f"Couldn't forget {ssid}")

    def wired_row(self, link: dict[str, str]) -> Button:
        device, connected = link["device"], link["state"] == "connected"
        if link["state"] == "unavailable":
            return list_row("󰈀", "Ethernet", text("Cable unplugged", "net-status"), icon_classes=("net-signal",))
        if device == self.pending:
            end = text("Connecting…", "net-status")
        else:
            end = check(connected)
        verb = "disconnect" if connected else "connect"
        return list_row(
            "󰈀",
            link["connection"] if connected else "Ethernet",
            end,
            classes=("default",) if connected else (),
            icon_classes=("net-signal",),
            on_clicked=lambda *_: self.run(device, ["device", verb, device], "Couldn't connect Ethernet"),
            tooltip="Disconnect" if connected else "Connect",
        )

    def ask(self, ssid: str, hint: str = "") -> None:
        self.asking, self.arming = ssid, ""
        self.entry.set_text("")
        self.set_hint(hint or (f"Enter the password for {ssid}" if ssid else ""), bool(hint))
        self.update(self.value)
        if ssid:
            self.take_focus()
            self.entry.grab_focus()

    def set_hint(self, message: str, error: bool = False) -> None:
        self.hint.set_text(message)
        flag(self.hint, "error", error)
        self.hint.set_visible(bool(message))

    def join(self, ssid: str, password: str = "") -> None:
        if not ssid:
            return
        args = ["device", "wifi", "connect", ssid]
        if password:
            args += ["password", password]  # ponytail: visible in the process list for a moment; passwd-file via a saved profile if that matters

        def failed(error: str) -> None:
            if "Secrets were required" in error or "psk" in error:
                self.ask(ssid, "Wrong password" if password else "")
            else:
                self.set_hint(f"Couldn't join {ssid}", True)

        self.asking = ""
        self.set_hint("")
        self.run(ssid, args, failed)

    def run(self, target: str, args: list[str], failed: str | Callable[[str], None]) -> None:
        self.pending, self.arming = target, ""

        def done(ok: bool, error: str) -> None:
            self.pending = ""
            if not ok:
                failed(error) if callable(failed) else self.set_hint(failed, True)
            self.update(self.value)

        nmcli(args, done)
        self.update(self.value)

    def on_radio(self, _switch: Gtk.Switch, active: bool) -> bool:
        if not self.syncing:
            nmcli(["radio", "wifi", "on" if active else "off"], lambda *_: None)
        return False


def build(context: Any) -> list[Any]:
    state = JsonState(SCRIPTS / "network.sh", {}, autostart=False)
    context.network_panels = [NetworkWindow(monitor, state, context.network) for monitor in context.monitors]
    return context.network_panels
