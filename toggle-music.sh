#!/bin/sh
# gdbus instead of `python -m fabric invoke-action`: no Python/GTK startup (~10ms vs ~400ms)
exec gdbus call --session --dest org.Fabric.fabric.fabric-shell --object-path /org/Fabric/fabric \
  --method org.Fabric.fabric.InvokeAction toggle-music '[]' >/dev/null
