#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
if [ "${TIMER_RETIREMENT_CI_EPHEMERAL:-}" != 1 ]; then
    echo 'This diagnostic requires an explicitly marked ephemeral checkout.' >&2
    exit 2
fi
suite=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$suite/../../../.." && pwd)
cd "$repo"
git -c safe.directory="$repo" diff --quiet
git -c safe.directory="$repo" diff --cached --quiet
base=b6c7d55ce6552b4aa85d0b6d866f50cb9ce62600
map=49c8aa941245bbee657a15b0ef605dbfb62ae9ae
fixed=567d337dc90852bfe89788ff7da726c421e7b596
git -c safe.directory="$repo" fetch --no-tags --depth=2 origin "$fixed"
git -c safe.directory="$repo" fetch --no-tags --depth=1 origin "$base"
directory="$repo/target/notingo-native-controls"
mkdir -p "$directory/retirement"
python3 "$suite/timeline_collector.py" "$directory/retirement" > "$directory/retirement/collector.log" 2>&1 &
collector=$!
trap 'kill "$collector" 2>/dev/null || true' EXIT
files=(kernel/core/src/lib.rs kernel/core/src/sched/sched_class/mod.rs kernel/core/src/sched/sched_class/fair.rs kernel/core/src/thread/mod.rs kernel/core/src/syscall/prctl.rs kernel/core/src/syscall/nanosleep.rs kernel/core/src/time/core/timer.rs kernel/core/src/time/softirq.rs kernel/core/src/time/clocks/system_wide.rs)
result=0
phases="heap map-lazy eager static"
if [ "${RETIREMENT_CONFIRM_ONLY:-0}" = 1 ]; then phases="heap static"; fi
for phase in $phases; do
    mkdir -p "$directory/retirement/$phase"
    echo "RETIREMENT_PHASE_BEGIN $phase"
    for file in "${files[@]}"; do
        if [ "$phase" = static ]; then ref=$base; else ref=HEAD; fi
        git -c safe.directory="$repo" show "$ref:$file" > "$file"
    done
    case "$phase" in
        heap) source=$base; variant=0 ;;
        map-lazy) source=$map; variant=1 ;;
        eager|static) source=$fixed; variant=2 ;;
    esac
    git -c safe.directory="$repo" show "$source:kernel/core/src/time/core/timer.rs" > kernel/core/src/time/core/timer.rs
    if [ "$phase" != static ]; then
        python3 "$suite/instrument_retirement.py" kernel/core/src/time/core/timer.rs "$variant"
        cargo fmt --package aster-core
    fi
    if [ -d target/osdk/linux-bzimage-setup ]; then
        mv target/osdk/linux-bzimage-setup "target/osdk/linux-bzimage-setup-before-$phase"
    fi
    if [ -f target/nixos/asterinas.img ]; then rm target/nixos/asterinas.img; fi
    make nixos NIXOS_TEST_SUITE=development-tools-go NIXOS_DISK_SIZE_IN_MB=8192 > "$directory/retirement/$phase/build.log" 2>&1
    if [ "$phase" = heap ]; then
        python3 "$suite/linux_controls.py"
        python3 "$suite/linux_controls.py" --check
        python3 "$suite/minimal_linux_controls.py"
        python3 "$suite/minimal_linux_controls.py" --check
        python3 "$suite/timeline_linux_controls.py"
    fi
    cp target/osdk/asterinas-run-base/Cargo.lock "$directory/retirement/$phase/Cargo.lock"
    sha256sum target/osdk/asterinas/asterinas-osdk-bin target/osdk/iso_root/boot/asterinas-osdk-bin > "$directory/retirement/$phase/kernel-sha256.txt"
    export PROBE_MINIMAL_SHA256=$(cat "$directory/minimal-sha256.txt")
    export PROBE_SHARED_SHA256=$(cat "$directory/sha256.txt")
    export PROBE_RETIREMENT_PHASE=$phase
    export PROBE_BASELINE_REQUIRED=1
    export PROBE_RUN_RUNTIME=1
    export PROBE_EXPECT_CPUS=1
    if [ "${RETIREMENT_CONFIRM_ONLY:-0}" = 1 ]; then export PROBE_RUN_RUNTIME=0; fi
    case_name=notingo_retirement_phase
    if [ "$phase" = static ]; then case_name=notingo_retirement_static; fi
    phase_result=0
    make run_nixos NIXOS_TEST_SUITE=development-tools-go NIXOS_TEST_TIMEOUT=12min NIXOS_TEST_CASE="$case_name" > "$directory/retirement/$phase/driver.log" 2>&1 || phase_result=$?
    cat "$directory/retirement/$phase/driver.log"
    if [ -f qemu.log ]; then mv qemu.log "$directory/retirement/$phase/qemu.log"; fi
    if [ -f qemu-serial.log ]; then mv qemu-serial.log "$directory/retirement/$phase/qemu-serial.log"; fi
    echo "RETIREMENT_PHASE_END $phase status=$phase_result"
    if [ "$phase_result" -ne 0 ]; then result=1; fi
    if [ "$phase" = static ]; then
        printf '%s\n' "$phase_result" > "$directory/retirement/static-status.txt"
        smp_result=0
        PROBE_RUN_RUNTIME=0 PROBE_EXPECT_CPUS=4 SMP=4 make run_nixos NIXOS_TEST_SUITE=development-tools-go NIXOS_TEST_TIMEOUT=12min NIXOS_TEST_CASE=notingo_retirement_static > "$directory/retirement/static/smp-driver.log" 2>&1 || smp_result=$?
        cat "$directory/retirement/static/smp-driver.log"
        if [ -f qemu.log ]; then mv qemu.log "$directory/retirement/static/smp-qemu.log"; fi
        if [ -f qemu-serial.log ]; then mv qemu-serial.log "$directory/retirement/static/smp-qemu-serial.log"; fi
        echo "RETIREMENT_SMP_END status=$smp_result"
        if [ "$smp_result" -ne 0 ]; then result=1; fi
    fi
done
shopt -s nullglob
traces=("$directory/retirement"/*.bin)
if [ "${#traces[@]}" -eq 0 ]; then exit 2; fi
python3 "$suite/timeline_decode.py" "${traces[@]}"
exit "$result"
