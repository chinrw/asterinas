#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
if [ "${TIMELINE_CI_EPHEMERAL:-}" != 1 ]; then
    echo 'This source-swapping diagnostic runs only in an explicitly marked ephemeral CI checkout.' >&2
    exit 2
fi
suite=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$suite/../../../.." && pwd)
cd "$repo"
git -c safe.directory="$repo" diff --quiet
git -c safe.directory="$repo" diff --cached --quiet
base=b6c7d55ce6552b4aa85d0b6d866f50cb9ce62600
git -c safe.directory="$repo" fetch --depth=1 --no-tags origin "$base"
files=(kernel/core/src/sched/sched_class/fair.rs kernel/core/src/lib.rs kernel/core/src/syscall/prctl.rs kernel/core/src/syscall/nanosleep.rs kernel/core/src/sched/sched_class/mod.rs kernel/core/src/thread/mod.rs kernel/core/src/time/core/timer.rs kernel/core/src/time/softirq.rs)
for file in "${files[@]}"; do git -c safe.directory="$repo" show "$base:$file" > "$file"; done
make nixos NIXOS_TEST_SUITE=development-tools-go NIXOS_DISK_SIZE_IN_MB=8192
python3 "$suite/minimal_linux_controls.py"
python3 "$suite/timeline_linux_controls.py"
directory="$repo/target/notingo-native-controls/timeline"
cp "$repo/target/osdk/asterinas-run-base/Cargo.lock" "$directory/uninstrumented-Cargo.lock"
sha256sum "$repo/target/osdk/asterinas/asterinas-osdk-bin" > "$directory/uninstrumented-kernel-sha256.txt"
baseline_result=0
make run_nixos NIXOS_TEST_SUITE=development-tools-go NIXOS_TEST_TIMEOUT=12min NIXOS_TEST_CASE=notingo_uninstrumented_timeline_baseline > "$directory/uninstrumented-driver.log" 2>&1 || baseline_result=$?
cat "$directory/uninstrumented-driver.log"
if [ -f qemu.log ]; then mv qemu.log "$directory/uninstrumented-qemu.log"; fi
if [ -f qemu-serial.log ]; then mv qemu-serial.log "$directory/uninstrumented-qemu-serial.log"; fi
for file in "${files[@]}"; do git -c safe.directory="$repo" show "HEAD:$file" > "$file"; done
rm target/nixos/asterinas.img
make nixos NIXOS_TEST_SUITE=development-tools-go NIXOS_DISK_SIZE_IN_MB=8192
cp "$repo/target/osdk/asterinas-run-base/Cargo.lock" "$directory/instrumented-Cargo.lock"
probe_result=0
bash "$suite/run_timeline_controls.sh" || probe_result=$?
echo "UNINSTRUMENTED_STATUS=$baseline_result TIMELINE_STATUS=$probe_result"
if [ "$baseline_result" -ne 0 ] || [ "$probe_result" -ne 0 ]; then exit 1; fi
