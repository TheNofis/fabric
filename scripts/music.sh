#!/bin/sh

pin="${XDG_RUNTIME_DIR:-/tmp}/fabric-music-player"

players() {
  busctl --user list --no-legend 2>/dev/null | awk '$1 ~ /^org\.mpris\.MediaPlayer2\./ { print $1 }'
}

status() {
  busctl --user get-property "$1" /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player PlaybackStatus 2>/dev/null
}

# playing > pinned (the source button) > paused > first; the iPhone (mpris-proxy) idles as Stopped
player() {
  set -- $(players)
  [ $# -gt 1 ] || { echo "${1-}"; return; }
  pinned=$(cat "$pin" 2>/dev/null) paused=
  for p; do
    case $(status "$p") in
      *Playing*) echo "$p"; return ;;
      *Paused*) [ -n "$paused" ] || paused=$p ;;
    esac
  done
  for p; do [ "$p" = "$pinned" ] && { echo "$p"; return; }; done
  echo "${paused:-$1}"
}

# mpris-proxy bridges a Bluetooth source (the iPhone's AVRCP) onto MPRIS
phone() {
  busctl --user list --no-legend 2>/dev/null | awk -v p="$1" '$1 == p { print ($3 == "mpris-proxy") ? "true" : "false"; f = 1 } END { if (!f) print "false" }'
}

# mpris-proxy caches the iPhone's position from the last play/pause/seek; bluez runs the live clock (ms)
# ponytail: first bluez player wins, pick by device if two phones ever stream at once
phone_position() {
  path=$(busctl --system tree org.bluez --list 2>/dev/null | grep -m1 -E '/player[0-9]+$') || { echo null; return; }
  busctl --system get-property org.bluez "$path" org.bluez.MediaPlayer1 Position 2>/dev/null | awk '{ print $2 * 1000; f = 1 } END { if (!f) print "null" }'
}

call() {
  busctl --user call "$1" /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player "$2" >/dev/null 2>&1
}

if [ "${1:-}" ] && [ "$1" != once ]; then
  player=$(player)
  [ -n "$player" ] || exit 0
  case "$1" in
    play-pause) call "$player" PlayPause ;;
    previous) call "$player" Previous ;;
    next) call "$player" Next ;;
    switch)
      # hand off: pause what plays here, pin the next source so it shows up ready to play
      set -- $(players) $(players)
      while [ $# -gt 0 ] && [ "$1" != "$player" ]; do shift; done
      [ -n "${2-}" ] && [ "$2" != "$player" ] || exit 0
      case $(status "$player") in *Playing*) call "$player" Pause ;; esac
      echo "$2" > "$pin"
      ;;
  esac
  exit 0
fi

# two busctl + one jq per tick
state() {
  player=$(player)
  if [ -z "$player" ]; then
    printf '%s\n' '{"status":"Stopped","title":"No media player","artist":"Start a player to see track details","art":"","position":0,"length":1,"elapsed":"0:00","duration":"0:00","source":"","phone":false,"players":0}'
    return
  fi
  phone=$(phone "$player")
  live=null
  [ "$phone" = true ] && live=$(phone_position)
  {
    busctl --user --json=short get-property "$player" /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player PlaybackStatus Position Metadata
    busctl --user --json=short get-property "$player" /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2 Identity
  } 2>/dev/null | jq -cs --argjson players "$(players | wc -l)" --argjson phone "$phone" --argjson live "$live" '
    def clock: (. / 1000000 | floor) as $s | "\($s / 60 | floor):\($s % 60 | if . < 10 then "0\(.)" else "\(.)" end)";
    def nonempty($fallback): if . == null or . == "" then $fallback else . end;
    (.[2].data // {}) as $m
    | ($live // .[1].data // 0) as $position
    | ($m["mpris:length"].data // 1) as $length
    | {
        status: (.[0].data | nonempty("Stopped")),
        title: ($m["xesam:title"].data | nonempty("Unknown track")),
        artist: ($m["xesam:artist"].data[0]? | nonempty("Unknown artist")),
        art: ($m["mpris:artUrl"].data // ""),
        position: $position,
        length: $length,
        elapsed: ($position | clock),
        duration: ($length | clock),
        source: (.[3].data // ""),
        phone: $phone,
        players: $players
      }'
}

state
[ "${1-}" = once ] && exit 0
while sleep 1; do state || exit; done
