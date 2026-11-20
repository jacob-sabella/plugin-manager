#!/usr/bin/env bash
# The emulator tests, from one command: the Plugin Manager inside real MPC firmware (QEngine), on a Gen1 and a Gen2 machine.
#   tests/emu/run.sh [-i INSTANCE]... [--label BUILD] [-k NAME]... [--prepare] [--list]
# Default instances: mpc-g1-391 (MPC OS 3.9.1, Gen1) and mpc-g2-3100 (3.10.0, Gen2), side by side. See tests/emu/README.md for the setup.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
inst=(); rest=()
while [ $# -gt 0 ]; do case "$1" in -i) inst+=("$2"); shift 2 ;; *) rest+=("$1"); shift ;; esac; done
[ ${#inst[@]} -gt 0 ] || inst=(mpc-g1-391 mpc-g2-3100)
mkdir -p "$here/results"
pids=()
for i in "${inst[@]}"; do
    ( "$here/in-container" "pm-emu-$i" python3 run.py "$i" ${rest[@]+"${rest[@]}"} 2>&1 | tee "$here/results/$i.log" | grep --line-buffered -E "^[0-9:]+ ($i|.*FAIL|      -)|Traceback|Error" ; exit "${PIPESTATUS[0]}" ) &
    pids+=($!)
done
rc=0
for p in "${pids[@]}"; do wait "$p" || rc=1; done
exit $rc
