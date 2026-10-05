#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python=${FABRIC_PYTHON:-"$HOME/.config/fabric/.venv/bin/python"}

[ -x "$python" ] || { echo "Fabric Python not found: $python" >&2; exit 1; }

pkill -u "$(id -u)" -x polybar 2>/dev/null || true

# Stop the previous instance by command line (a pidfile can point at a reused PID)
# and wait for it: it owns the fabric-shell D-Bus name and kills its script groups on exit.
pattern="$root/config.py"
if pkill -u "$(id -u)" -f "$pattern"; then
  i=0
  while pgrep -u "$(id -u)" -f "$pattern" >/dev/null && [ $i -lt 30 ]; do sleep 0.1; i=$((i + 1)); done
  pkill -KILL -u "$(id -u)" -f "$pattern" 2>/dev/null || true
fi

# bridges Bluetooth sources (iPhone over AVRCP) onto MPRIS for the music popup
pgrep -u "$(id -u)" -x mpris-proxy >/dev/null || setsid -f mpris-proxy >/dev/null 2>&1

export GDK_BACKEND=x11
exec "$python" "$root/config.py"
