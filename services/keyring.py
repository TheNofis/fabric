"""gnome-keyring system prompter: the dialog that unlocks/creates a keyring (browsers, Secret Service apps).

gnome-keyring asks whoever owns org.gnome.keyring.SystemPrompter on the session bus; without
the shell D-Bus activates gcr-prompter as before. Protocol (gcr/gcr-system-prompter.c):
  BeginPrompting(cb)                      -> queue the caller; when it's its turn: cb.PromptReady("", {}, exchange)
  PerformPrompt(cb, type, props, exchange) -> show the dialog; the answer: cb.PromptReady("yes"|"no", changed, exchange)
  StopPrompting(cb)                       -> close, cb.PromptDone()
The password travels encrypted by Gcr.SecretExchange. The dialog is our own: a Python Gcr.Prompt
can't work, PyGObject drops the async vfunc's user_data and gcr segfaults on reply.
"""

from __future__ import annotations

import sys
from typing import Any

import gi

gi.require_version("Gcr", "4")
from gi.repository import Gcr, Gio, GLib

BUS_NAME = "org.gnome.keyring.SystemPrompter"
OBJECT_PATH = "/org/gnome/keyring/Prompter"
CALLBACK = "org.gnome.keyring.internal.Prompter.Callback"
INTERFACE = Gio.DBusNodeInfo.new_for_xml("""
<node><interface name="org.gnome.keyring.internal.Prompter">
  <method name="BeginPrompting"><arg type="o" name="callback" direction="in"/></method>
  <method name="PerformPrompt">
    <arg type="o" name="callback" direction="in"/><arg type="s" name="type" direction="in"/>
    <arg type="a{sv}" name="properties" direction="in"/><arg type="s" name="exchange" direction="in"/>
  </method>
  <method name="StopPrompting"><arg type="o" name="callback" direction="in"/></method>
</interface></node>""").interfaces[0]


def label(value: str) -> str:
    return value.replace("_", "")  # GTK mnemonics from gcr ("_Unlock")


class Prompter:
    def __init__(self, ui: Any, bus: Gio.DBusConnection):
        self.ui, self.bus = ui, bus
        self.waiting: list[tuple[str, str]] = []  # (sender, callback path), served one at a time
        self.watches: dict[tuple[str, str], int] = {}
        self.active: tuple[str, str] | None = None
        self.exchange: Gcr.SecretExchange | None = None
        self.received = False  # the caller's half of the key exchange arrived
        self.pending = False  # the dialog waits for the user
        self.props: dict[str, Any] = {}

    def _call(self, _bus, sender: str, _path, _interface, method: str, args: GLib.Variant, invocation: Gio.DBusMethodInvocation) -> None:
        caller = (sender, args.unpack()[0])
        if method == "BeginPrompting":
            if caller in self.watches:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", "Already begun prompting for this prompt callback")
                return
            self.watches[caller] = Gio.bus_watch_name_on_connection(self.bus, sender, Gio.BusNameWatcherFlags.NONE, None, lambda *_: self._stop(caller, False))
            self.waiting.append(caller)
            invocation.return_value(None)
            self._next()
        elif method == "PerformPrompt":
            _, kind, props, received = args.unpack()
            if caller != self.active or self.pending:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", "Not ready for a prompt from this callback")
                return
            if not self.exchange.receive(received):
                invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", "Invalid secret exchange received")
                return
            self.received, self.pending, self.kind = True, True, kind
            self.props.update(props)
            invocation.return_value(None)
            self._show()
        else:  # StopPrompting
            self._stop(caller, True)
            invocation.return_value(None)

    def _show(self) -> None:
        p = self.props
        password = self.kind == "password"
        self.ui.open(p.get("message") or p.get("title") or "Authentication Required", p.get("description", ""))
        if p.get("warning"):
            self.ui.error(p["warning"])
        self.ui.prompt(
            "New keyring password" if p.get("password-new") else ("Password" if password else ""),
            password=password,
            repeat=bool(p.get("password-new")),
            choice=label(p.get("choice-label", "")),
            chosen=bool(p.get("choice-chosen")),
            ok=label(p.get("continue-label") or ("Unlock" if password else "Continue")),
            cancel=label(p.get("cancel-label") or "Cancel"),
        )

    def _next(self) -> None:
        if self.active is not None or not self.waiting:
            return
        self.active = self.waiting.pop(0)
        self.exchange, self.received, self.pending, self.props = Gcr.SecretExchange.new(None), False, False, {}
        self._ready("", None, {})

    def _ready(self, reply: str, secret: str | None, changed: dict[str, GLib.Variant]) -> None:
        sent = self.exchange.send(secret, -1) if self.received else self.exchange.begin()
        sender, path = self.active
        params = GLib.Variant("(sa{sv}s)", (reply, changed, sent))
        caller = self.active
        self.bus.call(sender, path, CALLBACK, "PromptReady", params, None, Gio.DBusCallFlags.NO_AUTO_START, -1, None, lambda bus, result: self._ready_done(caller, bus, result))

    def _ready_done(self, caller: tuple[str, str], bus: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        try:
            bus.call_finish(result)
        except GLib.Error as error:
            print(f"keyring prompter: {error.message}", file=sys.stderr)
            self._stop(caller, False)

    def respond(self, password: str, chosen: bool) -> None:
        if not self.pending:
            return
        self.pending = False
        changed = {}
        if self.props.get("choice-label"):
            changed["choice-chosen"] = GLib.Variant("v", GLib.Variant("b", chosen))  # gcr wraps values twice
        if self.props.get("password-new"):
            changed["password-strength"] = GLib.Variant("v", GLib.Variant("i", 1 if password else 0))
        # the dialog stays open (busy) until gnome-keyring prompts again (wrong password) or stops
        self._ready("yes", password if self.kind == "password" else None, changed)

    def cancel(self) -> None:
        if self.pending:
            self.pending = False
            self._ready("no", None, {})

    def _stop(self, caller: tuple[str, str], send_done: bool) -> None:
        watch = self.watches.pop(caller, None)
        if watch is None:
            return
        Gio.bus_unwatch_name(watch)
        if caller in self.waiting:
            self.waiting.remove(caller)
        if caller == self.active:
            self.active, self.pending = None, False
            self.ui.close()
        if send_done:
            self.bus.call(caller[0], caller[1], CALLBACK, "PromptDone", None, None, Gio.DBusCallFlags.NO_AUTO_START, -1, None, None)
        self._next()


def start(ui: Any) -> Prompter | None:
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        prompter = Prompter(ui, bus)
        bus.register_object(OBJECT_PATH, INTERFACE, prompter._call, None, None)
    except GLib.Error as error:
        print(f"keyring prompter: {error.message}", file=sys.stderr)
        return None
    # REPLACE takes the name from a gcr-prompter that happens to be running
    flags = Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT | Gio.BusNameOwnerFlags.REPLACE
    prompter.owner = Gio.bus_own_name_on_connection(bus, BUS_NAME, flags, None, lambda *_: print(f"keyring prompter: lost {BUS_NAME}", file=sys.stderr))
    return prompter
