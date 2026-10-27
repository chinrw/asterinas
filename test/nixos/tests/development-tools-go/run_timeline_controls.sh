#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
suite=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$suite/../../../.." && pwd)
directory="$repo/target/notingo-native-controls/timeline"
mkdir -p "$directory"
python3 "$suite/timeline_collector.py" "$directory" > "$directory/collector.log" 2>&1 &
collector_pid=$!
trap 'kill "$collector_pid" 2>/dev/null || true' EXIT
result=0
PROBE_SHARED_SHA256=$(cat "$repo/target/notingo-native-controls/sha256.txt") PROBE_MINIMAL_SHA256=$(cat "$repo/target/notingo-native-controls/minimal-sha256.txt") PROBE_TIMELINE_DIGESTS=$(cat "$directory/digests.txt") make run_nixos \
 NIXOS_TEST_SUITE=development-tools-go NIXOS_TEST_TIMEOUT=12min \
 NIXOS_TEST_CASE=notingo_timer_budget_capacity > "$directory/driver.log" 2>&1 || result=$?
cat "$directory/driver.log"
if [ -f "$repo/qemu.log" ]; then mv "$repo/qemu.log" "$directory/qemu.log"; fi
if [ -f "$repo/qemu-serial.log" ]; then mv "$repo/qemu-serial.log" "$directory/qemu-serial.log"; fi
shopt -s nullglob
traces=("$directory"/*.bin)
if [ "${#traces[@]}" -gt 0 ]; then python3 "$suite/timeline_decode.py" "${traces[@]}"; else echo TIMELINE_NO_TRACE_FILES; exit 2; fi
exit "$result"
