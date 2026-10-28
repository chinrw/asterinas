#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# One sample per boot.  The timeout is enforced by the host: rexpect's
# NIXOS_TEST_TIMEOUT bounds the serial read, and the outer `timeout` kills
# the whole QEMU/make process if the guest cannot even reach that point.
set -euo pipefail
suite=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$suite/../../../.." && pwd)
image="$repo/target/nixos/asterinas.img"
clean_image="$repo/target/nixos/notingo-host-watchdog-clean.img"
cp --sparse=always "$image" "$clean_image"

samples=(minimal minimal minimal minimal minimal minimal minimal minimal minimal minimal minimal minimal minimal minimal minimal minimal minimal minimal minimal minimal sleep20 sleep1000)
combined_status=0
for index in "${!samples[@]}"; do
    name="${samples[$index]}"
    cp --sparse=always "$clean_image" "$image"
    log="$repo/target/notingo-native-controls/host-watchdog-$index-$name.log"
    mkdir -p "$(dirname "$log")"
    started=$(date +%s)
    result=0
    PROBE_HOST_SAMPLE="$name" timeout -k 5s 90s make run_nixos \
        NIXOS_TEST_SUITE=development-tools-go \
        NIXOS_TEST_TIMEOUT=60s \
        NIXOS_TEST_CASE=notingo_host_sample > "$log" 2>&1 || result=$?
    elapsed=$(( $(date +%s) - started ))
    cat "$log"
    # A passed sample can still leave `make` waiting for QEMU cleanup.  The
    # sample result is authoritative; do not turn a cleanup hang into a
    # benchmark failure.
    if [ "$result" -ne 0 ] && grep -q "Test case 'notingo_host_sample' passed" "$log"; then
        echo "HOST_CLEANUP_TIMEOUT $index $name (sample passed)"
        result=0
    fi
    echo "HOST_WATCHDOG_SAMPLE $index $name STATUS=$result SECONDS=$elapsed"
    if [ "$result" -ne 0 ]; then
        combined_status=1
    fi
    # The outer timeout can leave the QEMU child behind when make is killed.
    pkill -f "qemu-system-x86_64.*asterinas.img" 2>/dev/null || true
done

rm "$clean_image"
exit "$combined_status"
