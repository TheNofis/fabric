#!/bin/sh
# SSH_ASKPASS: the passphrase / yes-no prompts of ssh, ssh-add and git open the shell's auth dialog.
# Prints the answer; exits 1 on cancel or with no shell running, as ssh expects.
[ "${SSH_ASKPASS_PROMPT:-}" = none ] && exit 0  # info-only notice ("touch your key"): nothing to answer
exec "$HOME/.config/fabric/.venv/bin/python" - "${1:-}" "${SSH_ASKPASS_PROMPT:-}" <<'PY'
import sys
from gi.repository import Gio, GLib
try:
    ok, answer = Gio.bus_get_sync(Gio.BusType.SESSION).call_sync(
        "org.fabric.Askpass", "/org/fabric/Askpass", "org.fabric.Askpass", "Ask",
        GLib.Variant("(sb)", (sys.argv[1], sys.argv[2] == "confirm")), GLib.VariantType("(bs)"),
        Gio.DBusCallFlags.NO_AUTO_START, GLib.MAXINT, None).unpack()
except GLib.Error as error:
    sys.exit(f"askpass: {error.message}")
if not ok:
    sys.exit(1)
print(answer)
PY
