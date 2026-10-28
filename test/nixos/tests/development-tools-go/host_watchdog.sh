#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# One sample per boot.  The sample itself is bounded by a host-side 3s
# watchdog in the Rust test.  This driver additionally watches the test log
# and kills QEMU as soon as the pass/fail marker appears, so a stuck QEMU
# cleanup cannot consume the outer timeout or the CI job budget.
set -euo pipefail
suite=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$suite/../../../.." && pwd)
image="$repo/target/nixos/asterinas.img"
clean_image="$repo/target/nixos/notingo-host-watchdog-clean.img"
cp --sparse=always "$image" "$clean_image"

samples=(
    minimal minimal minimal minimal minimal
    minimal minimal minimal minimal minimal
    minimal minimal minimal minimal minimal
    minimal minimal minimal minimal minimal
    minimal minimal minimal minimal minimal
    sleep20 sleep1000
)
combined_status=0

for index in "${!samples[@]}"; do
    name="${samples[$index]}"
    cp --sparse=always "$clean_image" "$image"
    log="$repo/target/notingo-native-controls/host-watchdog-$index-$name.log"
    mkdir -p "$(dirname "$log")"
    started=$(date +%s)

    PROBE_HOST_SAMPLE="$name" make run_nixos \
        NIXOS_TEST_SUITE=development-tools-go \
        NIXOS_TEST_TIMEOUT=60s \
        NIXOS_TEST_CASE=notingo_host_sample > "$log" 2>&1 &
    driver_pid=$!

    outcome=timeout
    for _ in $(seq 1 900); do
        if grep -q "Test case 'notingo_host_sample' passed" "$log"; then
            outcome=pass
            break
        fi
        if grep -q "Test case 'notingo_host_sample' failed" "$log"; then
            outcome=fail
            break
        fi
        if ! kill -0 "$driver_pid" 2>/dev/null; then
            if wait "$driver_pid"; then
                outcome=pass
            else
                outcome=fail
            fi
            break
        fi
        sleep 0.2
    done

    if [ "$outcome" = "timeout" ]; then
        echo "HOST_WATCHDOG_TIMEOUT $index $name"
    fi
    kill -TERM "$driver_pid" 2>/dev/null || true
    sleep 1
    pkill -9 -f "qemu-system-x86_64.*asterinas.img" 2>/dev/null || true
    pkill -9 -f "test-development-tools-go --qemu-cmd" 2>/dev/null || true
    wait "$driver_pid" 2>/dev/null || true

    elapsed=$(( $(date +%s) - started ))
    cat "$log"
    echo "HOST_WATCHDOG_SAMPLE $index $name OUTCOME=$outcome SECONDS=$elapsed"
    if [ "$outcome" != "pass" ]; then
        combined_status=1
    fi
done

rm "$clean_image"
exit "$combined_status"
