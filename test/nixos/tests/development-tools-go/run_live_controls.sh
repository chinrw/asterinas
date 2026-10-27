#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
suite=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$suite/../../../.." && pwd)
image="$repo/target/nixos/asterinas.img"
clean_image="$repo/target/nixos/notingo-clean.img"
cp --sparse=always "$image" "$clean_image"
combined_status=0

run_case() {
    local case_name=$1
    local timeout=$2
    local directory="$repo/target/notingo-native-controls/$case_name"
    mkdir -p "$directory"
    cp --sparse=always "$clean_image" "$image"
    local result=0
    make run_nixos \
        NIXOS_TEST_SUITE=development-tools-go \
        NIXOS_TEST_TIMEOUT="$timeout" \
        NIXOS_TEST_CASE="$case_name" > "$directory/driver.log" 2>&1 || result=$?
    cat "$directory/driver.log"
    echo "CASE=$case_name GUEST_STATUS=$result"
    if [ -f "$repo/qemu.log" ]; then mv "$repo/qemu.log" "$directory/qemu.log"; fi
    if [ -f "$repo/qemu-serial.log" ]; then mv "$repo/qemu-serial.log" "$directory/qemu-serial.log"; fi
    if [ "$result" -ne 0 ]; then combined_status=1; fi
}

run_case notingo_benchmark 12min

rm "$clean_image"
exit "$combined_status"
