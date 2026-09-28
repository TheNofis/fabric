#!/bin/sh
# toggle default sink between the G435 headset and the built-in card
sink() { pactl list short sinks | awk -v p="$1" '$2 ~ p { print $2; exit }'; }
case $(pactl get-default-sink) in
  *G435*) target=$(sink '^alsa_output[.]pci-0000_00_1f[.]3[.]') ;;
  *) target=$(sink 'G435') ;;
esac
[ -n "$target" ] && pactl set-default-sink "$target"
