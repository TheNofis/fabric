#!/bin/sh
ws() { i3-msg -t get_workspaces | jq -c 'map({name, focused, visible, urgent, output})'; }
ws
i3-msg -t subscribe -m '["workspace","output"]' | while read -r _; do ws || exit; done
