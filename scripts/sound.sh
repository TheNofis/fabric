#!/bin/sh
# every sink and source plus the defaults as one JSON line, again on each device/server change;
# stream (sink-input) events are ignored, so playback doesn't spam the sound panel
state() {
  printf '{"sink":"%s","source":"%s","sinks":%s,"sources":%s}\n' \
    "$(pactl get-default-sink)" "$(pactl get-default-source)" \
    "$(pactl -f json list sinks)" "$(pactl -f json list sources)"
}

state
cleanup() {
  trap - EXIT INT TERM
  pkill -P $$ 2>/dev/null || true
}
trap cleanup EXIT INT TERM
pactl subscribe | grep --line-buffered -E "on (sink|source|server) #" | while read -r _; do
  state || exit
done
