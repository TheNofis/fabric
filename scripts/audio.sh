#!/bin/sh
# emits {"vol":N,"muted":bool} on every sink/server change (the default sink may have changed)

state() {
  # "Volume: 0.45 [MUTED]" -> 45, parsed in-shell (wpctl always prints two decimals)
  volume=$(wpctl get-volume @DEFAULT_AUDIO_SINK@) || volume="Volume: 0.00"
  v=${volume#Volume: }; v=${v%% *}
  case $v in *.??) v=$(( ${v%.*} * 100 + 1${v#*.} - 100 )) ;; *) v=0 ;; esac
  case $volume in *MUTED*) m=true ;; *) m=false ;; esac
  printf '{"vol":%s,"muted":%s}\n' "$v" "$m"
}

state
[ "${1-}" = once ] && exit 0

cleanup() {
  trap - EXIT INT TERM
  pkill -P $$ 2>/dev/null || true
}
trap cleanup EXIT INT TERM
# sink events: only the volume can change -> one wpctl; the default sink changes via 'server'
pactl subscribe | grep --line-buffered -E "'change' on (sink|server)" | while read -r _; do
  state || exit
done
