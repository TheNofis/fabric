#!/bin/bash
# NetworkManager as JSON lines: raw `nmcli -t` tables (the panel parses them), again on every
# nmcli monitor event (bursts coalesced) and every 5s for fresh scan results.
# Runs only while the network panel is open.

known() {  # uuid:ssid of saved Wi-Fi profiles (profile names may differ from the SSID)
  nmcli -t -f UUID,TYPE connection show | while IFS=: read -r uuid type; do
    [ "$type" = 802-11-wireless ] && echo "$uuid:$(nmcli -g 802-11-wireless.ssid connection show "$uuid")"
  done
}

state() {
  printf '{"radio":"%s","wifi":%s,"devices":%s,"known":%s}\n' \
    "$(nmcli -t -f WIFI radio)" \
    "$(nmcli -t -f IN-USE,SIGNAL,SECURITY,SSID device wifi list --rescan no | jq -Rs .)" \
    "$(nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device | jq -Rs .)" \
    "$(known | jq -Rs .)"
}

nmcli device wifi rescan >/dev/null 2>&1 &
state
tick=0
while :; do
  if read -r -t 5 _; then
    while read -r -t 0.3 _; do :; done  # one state per burst of events
  elif [ $((tick += 1)) -ge 6 ]; then
    tick=0
    nmcli device wifi rescan >/dev/null 2>&1 &  # results show up on the next tick
  fi
  state || exit
done < <(nmcli monitor)
