#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
[ "${TIMER_RETIREMENT_CI_EPHEMERAL:-}" = 1 ]
suite=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$suite/../../../.." && pwd)
cd "$repo"
git -c safe.directory="$repo" diff --quiet
git -c safe.directory="$repo" diff --cached --quiet
base=b6c7d55ce6552b4aa85d0b6d866f50cb9ce62600
fixed=567d337dc90852bfe89788ff7da726c421e7b596
git -c safe.directory="$repo" fetch --no-tags --depth=1 origin "$base"
git -c safe.directory="$repo" fetch --no-tags --depth=1 origin "$fixed"
for file in kernel/core/src/lib.rs kernel/core/src/sched/sched_class/mod.rs kernel/core/src/sched/sched_class/fair.rs kernel/core/src/thread/mod.rs kernel/core/src/syscall/prctl.rs kernel/core/src/syscall/nanosleep.rs kernel/core/src/time/core/timer.rs kernel/core/src/time/softirq.rs kernel/core/src/time/clocks/system_wide.rs; do
    git -c safe.directory="$repo" show "$base:$file" > "$file"
done
git -c safe.directory="$repo" show "$fixed:kernel/core/src/time/core/timer.rs" > kernel/core/src/time/core/timer.rs
out="$repo/target/notingo-native-controls/integration"
mkdir -p "$out"
if [ -d target/osdk/linux-bzimage-setup ]; then mv target/osdk/linux-bzimage-setup target/osdk/linux-bzimage-setup-before-integration; fi
make nixos NIXOS_TEST_SUITE=development-tools-go NIXOS_DISK_SIZE_IN_MB=8192 > "$out/build.log" 2>&1
python3 "$suite/minimal_linux_controls.py"
python3 "$suite/minimal_linux_controls.py" --check
python3 "$suite/linux_controls.py"
python3 "$suite/linux_controls.py" --check
package=$(nix-build --no-out-link --expr 'import ./test/nixos/tests/development-tools-go/retirement_regressions.nix { pkgs = import ./distro/nixpkgs.nix {}; }')
sha256sum "$package/bin/timer_integration" > "$out/integration-binary-sha256.txt"
for mode in stress teardown burst; do
    timeout --signal=KILL 120s "$package/bin/timer_integration" "$mode" 0 > "$out/linux-$mode.log" 2>&1
    cat "$out/linux-$mode.log"
done
cp target/osdk/asterinas-run-base/Cargo.lock "$out/Cargo.lock"
sha256sum target/osdk/asterinas/asterinas-osdk-bin target/osdk/iso_root/boot/asterinas-osdk-bin > "$out/kernel-sha256.txt"
export PROBE_MINIMAL_SHA256=$(cat target/notingo-native-controls/minimal-sha256.txt)
export PROBE_SHARED_SHA256=$(cat target/notingo-native-controls/sha256.txt)
export PROBE_RUN_RUNTIME=0
result=0
for cpus in 1 4; do
    mkdir -p "$out/guest-$cpus"
    export PROBE_EXPECT_CPUS=$cpus SMP=$cpus
    guest_result=0
    make run_nixos NIXOS_TEST_SUITE=development-tools-go NIXOS_TEST_TIMEOUT=8min NIXOS_TEST_CASE=notingo_retirement_integration > "$out/guest-$cpus/driver.log" 2>&1 || guest_result=$?
    cat "$out/guest-$cpus/driver.log"
    for file in qemu.log qemu-serial.log; do
        if [ -f "$file" ]; then mv "$file" "$out/guest-$cpus/$file"; fi
    done
    echo "INTEGRATION_GUEST_END cpus=$cpus status=$guest_result"
    if [ "$guest_result" -ne 0 ]; then result=1; fi
done
exit "$result"
