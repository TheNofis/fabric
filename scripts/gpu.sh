#!/bin/sh
# NVIDIA GPU as JSON lines every 2s; runs only while the system monitor is open.
# Fields nvidia-smi reports as [N/A] become null.

command -v nvidia-smi >/dev/null || exec sleep infinity  # no NVIDIA: stay quiet instead of respawning

nvidia-smi --query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw \
  --format=csv,noheader,nounits -lms 2000 |
  awk -F', ' '
    function num(v) { return v ~ /^[0-9.]+$/ ? v : "null" }
    { printf "{\"name\":\"%s\",\"load\":%s,\"temp\":%s,\"mem_used\":%s,\"mem_total\":%s,\"power\":%s}\n",
        $1, num($2), num($3), num($4), num($5), num($6); fflush() }'
