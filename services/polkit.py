"""Polkit authentication agent: answers the password prompts of pkexec, systemctl, GParted...

polkitd calls BeginAuthentication on the agent registered for our login session. The
agent drives a PolkitAgent.Session (which runs the setuid polkit-agent-helper-1) and
talks to the UI through open(title, message) / prompt(label, ...) / error(text) / close().

The D-Bus interface is exported by hand: a Python PolkitAgent.Listener subclass can't
work, PyGObject drops the async vfunc's user_data and libpolkit-agent segfaults on reply.
"""

from __future__ import annotations

import locale
import os
import sys
from typing import Any

import gi

gi.require_version("Polkit", "1.0")
gi.require_version("PolkitAgent", "1.0")
from gi.repository import Gio, GLib, Polkit, PolkitAgent

OBJECT_PATH = "/org/fabric/PolicyKit1/AuthenticationAgent"
INTERFACE = Gio.DBusNodeInfo.new_for_xml("""
<node><interface name="org.freedesktop.PolicyKit1.AuthenticationAgent">
  <method name="BeginAuthentication">
    <arg type="s" name="action_id" direction="in"/><arg type="s" name="message" direction="in"/>
    <arg type="s" name="icon_name" direction="in"/><arg type="a{ss}" name="details" direction="in"/>
    <arg type="s" name="cookie" direction="in"/><arg type="a(sa{sv})" name="identities" direction="in"/>
  </method>
  <method name="CancelAuthentication"><arg type="s" name="cookie" direction="in"/></method>
</interface></node>""").interfaces[0]


def pick_identity(identities: list[tuple[str, dict[str, Any]]], uid: int) -> tuple[str, dict[str, Any]]:
    """Our own user when polkit accepts it (admin via wheel), otherwise the first user offered (root)."""
    users = [item for item in identities if item[0] == "unix-user"]
    return next((item for item in users if item[1]["uid"] == uid), (users or identities)[0])


class Agent:
    def __init__(self, ui: Any):
        self.ui = ui
        self.invocation: Gio.DBusMethodInvocation | None = None
        self.session: PolkitAgent.Session | None = None
        self.cookie = ""
        self.cancelled = False

    def _call(self, _bus, _sender, _path, _interface, method: str, args: GLib.Variant, invocation: Gio.DBusMethodInvocation) -> None:
        if method == "CancelAuthentication":  # the requester went away
            if args.unpack()[0] == self.cookie:
                self.cancel()
            invocation.return_value(None)
            return
        _action, message, _icon, _details, cookie, identities = args.unpack()
        if self.invocation is not None:
            # ponytail: one dialog at a time, a concurrent request is denied; queue if that ever bites
            invocation.return_dbus_error("org.freedesktop.PolicyKit1.Error.Failed", "Another authentication is in progress")
            return
        kind, details = pick_identity(identities, os.getuid())
        if kind == "unix-user":
            self.identity = Polkit.UnixUser.new(details["uid"])
            self.name = self.identity.get_name() or str(details["uid"])
        else:  # ponytail: only groups besides users; netgroups never show up on a desktop
            self.identity = Polkit.UnixGroup.new(details["gid"])
            self.name = self.identity.to_string()
        self.invocation, self.cookie, self.cancelled = invocation, cookie, False
        self.ui.open("Authentication Required", message)
        self._start()

    def _start(self) -> None:
        session = PolkitAgent.Session.new(self.identity, self.cookie)
        session.connect("request", lambda _s, text, echo: self.ui.prompt(f"{text.strip().rstrip(':')} for {self.name}", echo=echo))
        session.connect("show-error", lambda _s, text: self.ui.error(text))
        session.connect("show-info", lambda _s, text: self.ui.error(text))
        session.connect("completed", self._completed)
        self.session = session
        session.initiate()

    def respond(self, password: str, _chosen: bool = False) -> None:
        if self.invocation is not None:
            self.session.response(password)

    def cancel(self) -> None:
        if self.invocation is not None and not self.cancelled:
            self.cancelled = True
            self.session.cancel()  # emits completed(False)

    def _completed(self, _session: Any, gained: bool) -> None:
        if not gained and not self.cancelled:
            self.ui.error("Wrong password")
            self._start()  # polkit keeps the request open; a fresh helper asks again
            return
        invocation, self.invocation = self.invocation, None
        self.ui.close()
        # polkitd learns the result from the helper; the reply only ends the request
        if gained:
            invocation.return_value(None)
        else:
            invocation.return_dbus_error("org.freedesktop.PolicyKit1.Error.Cancelled", "Authentication dismissed")


def start(ui: Any) -> Agent | None:
    """Register for this login session; None if another agent (polkit-gnome...) already owns it."""
    agent = Agent(ui)
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        bus.register_object(OBJECT_PATH, INTERFACE, agent._call, None, None)
        subject = Polkit.UnixSession.new_for_process_sync(os.getpid(), None)
        lang = locale.getlocale()[0] or "C"
        agent.authority = Polkit.Authority.get_sync(None)  # kept: freeing it drops the registration
        agent.authority.register_authentication_agent_sync(subject, lang, OBJECT_PATH, None)
    except (GLib.Error, TypeError) as error:
        print(f"polkit agent: {getattr(error, 'message', error)}", file=sys.stderr)
        return None
    return agent
