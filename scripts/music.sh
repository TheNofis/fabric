#!/bin/sh

# the playing player wins; otherwise the first one (was: always the first, even if paused)
player() {
  players=$(busctl --user list --no-legend 2>/dev/null | awk '$1 ~ /^org\.mpris\.MediaPlayer2\./ { print $1 }')
  set -- $players
  [ $# -gt 1 ] && for p; do
    case $(busctl --user get-property "$p" /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player PlaybackStatus 2>/dev/null) in
      *Playing*) echo "$p"; return ;;
    esac
  done
  echo "${1-}"
}

if [ "${1:-}" ] && [ "$1" != once ]; then
  player=$(player)
  [ -n "$player" ] || exit 0
  case "$1" in
    play-pause) method=PlayPause ;;
    previous) method=Previous ;;
    next) method=Next ;;
    *) exit 0 ;;
  esac
  busctl --user call "$player" /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player "$method" >/dev/null
  exit 0
fi

# one busctl + one jq per tick (was ~10 processes)
state() {
  player=$(player)
  if [ -z "$player" ]; then
    printf '%s\n' '{"status":"Stopped","title":"No media player","artist":"Start a player to see track details","art":"","position":0,"length":1,"elapsed":"0:00","duration":"0:00"}'
    return
  fi
  busctl --user --json=short get-property "$player" /org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player \
    PlaybackStatus Position Metadata 2>/dev/null | jq -cs '
    def clock: (. / 1000000 | floor) as $s | "\($s / 60 | floor):\($s % 60 | if . < 10 then "0\(.)" else "\(.)" end)";
    def nonempty($fallback): if . == null or . == "" then $fallback else . end;
    (.[2].data // {}) as $m
    | (.[1].data // 0) as $position
    | ($m["mpris:length"].data // 1) as $length
    | {
        status: (.[0].data | nonempty("Stopped")),
        title: ($m["xesam:title"].data | nonempty("Unknown track")),
        artist: ($m["xesam:artist"].data[0]? | nonempty("Unknown artist")),
        art: ($m["mpris:artUrl"].data // ""),
        position: $position,
        length: $length,
        elapsed: ($position | clock),
        duration: ($length | clock)
      }'
}

state
[ "${1-}" = once ] && exit 0
while sleep 1; do state || exit; done
