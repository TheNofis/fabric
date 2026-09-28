#!/bin/sh
# emits {"vol":N,"muted":bool,"dev":"..."} on every sink/server change
device() { case $(pactl get-default-sink) in *G435*) d=G435 ;; *) d=CCA-CRA ;; esac; }

state() {
  # "Volume: 0.45 [MUTED]" -> 45, parsed in-shell (wpctl always prints two decimals)
  volume=$(wpctl get-volume @DEFAULT_AUDIO_SINK@) || volume="Volume: 0.00"
  v=${volume#Volume: }; v=${v%% *}
  case $v in *.??) v=$(( ${v%.*} * 100 + 1${v#*.} - 100 )) ;; *) v=0 ;; esac
  case $volume in *MUTED*) m=true ;; *) m=false ;; esac
  printf '{"vol":%s,"muted":%s,"dev":"%s"}\n' "$v" "$m" "$d"
}

device
state
[ "${1-}" = once ] && exit 0

cleanup() {
  trap - EXIT INT TERM
  pkill -P $$ 2>/dev/null || true
}
trap cleanup EXIT INT TERM
# sink events: only the volume can change -> one wpctl; the default sink changes via 'server'
pactl subscribe | grep --line-buffered -E "'change' on (sink|server)" | while read -r event; do
  case $event in *server*) device ;; esac
  state || exit
done
