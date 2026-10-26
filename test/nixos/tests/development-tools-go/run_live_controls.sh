#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
suite=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$suite/../../../.." && pwd)
image="$repo/target/nixos/asterinas.img"
clean_image="$repo/target/nixos/notingo-clean.img"
cp --sparse=always "$image" "$clean_image"
combined_status=0
for variant in default periodic; do
    directory="$repo/target/notingo-native-controls/live-$variant"
    mkdir -p "$directory"
    cp --sparse=always "$clean_image" "$image"
    python3 "$suite/live_monitor.py" "$directory" > "$directory/monitor.log" 2>&1 &
    monitor_pid=$!
    result=0
    PROBE_CPU_VARIANT=$variant QEMU_BIN="$suite/qemu_probe.py" make run_nixos \
        NIXOS_TEST_SUITE=development-tools-go NIXOS_TEST_TIMEOUT=12min \
        NIXOS_TEST_CASE=notingo_live_controls > "$directory/driver.log" 2>&1 || result=$?
    touch "$directory/done"
    monitor_result=0
    wait "$monitor_pid" || monitor_result=$?
    cat "$directory/driver.log"
    echo "CPU_VARIANT=$variant GUEST_STATUS=$result MONITOR_STATUS=$monitor_result"
    if [ -f "$repo/qemu.log" ]; then mv "$repo/qemu.log" "$directory/qemu.log"; fi
    if [ -f "$repo/qemu-serial.log" ]; then mv "$repo/qemu-serial.log" "$directory/qemu-serial.log"; fi
    if [ "$result" -ne 0 ] || [ "$monitor_result" -ne 0 ]; then combined_status=1; fi
done
rm "$clean_image"
exit "$combined_status"
