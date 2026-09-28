#!/bin/sh
# gdbus: see toggle-launcher.sh
exec gdbus call --session --dest org.Fabric.fabric.fabric-shell --object-path /org/Fabric/fabric \
  --method org.Fabric.fabric.InvokeAction lock '[]' >/dev/null
