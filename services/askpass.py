"""SSH askpass: ssh, ssh-add and git run $SSH_ASKPASS (askpass.sh) for a passphrase or a yes/no,
and askpass.sh asks us over D-Bus: Ask(prompt, confirm) -> (ok, answer).

The reply waits until the dialog is answered or dismissed. ssh re-runs askpass on a wrong
passphrase ("Bad passphrase, try again for ..."), so every request is a fresh dialog.
"""

from __future__ import annotations

import sys
from typing import Any

from gi.repository import Gio, GLib

BUS_NAME = "org.fabric.Askpass"
OBJECT_PATH = "/org/fabric/Askpass"
INTERFACE = Gio.DBusNodeInfo.new_for_xml("""
<node><interface name="org.fabric.Askpass">
  <method name="Ask">
    <arg type="s" name="prompt" direction="in"/><arg type="b" name="confirm" direction="in"/>
    <arg type="b" name="ok" direction="out"/><arg type="s" name="answer" direction="out"/>
  </method>
</interface></node>""").interfaces[0]


class Agent:
    def __init__(self, ui: Any):
        self.ui = ui
        self.invocation: Gio.DBusMethodInvocation | None = None
        self.confirm = False

    def _call(self, _bus, _sender, _path, _interface, _method, args: GLib.Variant, invocation: Gio.DBusMethodInvocation) -> None:
        if self.invocation is not None:
            # ponytail: one dialog at a time, a concurrent request is refused (ssh falls back to failing)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", "Another prompt is open")
            return
        prompt, confirm = args.unpack()
        prompt = prompt.strip()
        # ssh-add -c sets SSH_ASKPASS_PROMPT=confirm; an unknown host key asks to type "yes"
        self.invocation, self.confirm = invocation, confirm or "(yes/no" in prompt
        self.ui.open("SSH", prompt.rstrip(":"))
        if prompt.startswith("Bad passphrase"):
            self.ui.error("Wrong passphrase")
        if self.confirm:
            self.ui.prompt("", password=False, ok="Allow", cancel="Deny")
        else:
            self.ui.prompt("", ok="Unlock")

    def _reply(self, ok: bool, answer: str) -> None:
        invocation, self.invocation = self.invocation, None
        if invocation is not None:
            invocation.return_value(GLib.Variant("(bs)", (ok, answer)))

    def respond(self, password: str, _chosen: bool = False) -> None:
        self._reply(True, "yes" if self.confirm else password)
        self.ui.close()

    def cancel(self) -> None:
        self._reply(False, "")


def start(ui: Any) -> Agent | None:
    agent = Agent(ui)
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.register_object(OBJECT_PATH, INTERFACE, agent._call, None, None)
    except GLib.Error as error:
        print(f"askpass: {error.message}", file=sys.stderr)
        return None
    agent.owner = Gio.bus_own_name_on_connection(bus, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, lambda *_: print(f"askpass: lost {BUS_NAME}", file=sys.stderr))
    return agent
