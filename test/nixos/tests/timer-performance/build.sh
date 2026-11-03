#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
[ "${TIMER_BENCHMARK_EPHEMERAL:-}" = 1 ]
suite=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$suite/../../../.." && pwd)
cd "$repo"
base=e0d8b839f95f608c8a7e9c8942e01614a3b3b260
fixed=64848d3e0a23ff0903f4be485fb7750c1709a575
out="$repo/target/timer-performance"
mkdir -p "$out"
git -c safe.directory="$repo" fetch --no-tags --depth=1 origin "$base"
git -c safe.directory="$repo" fetch --no-tags --depth=1 origin "$fixed"
git -c safe.directory="$repo" diff --exit-code "$fixed" -- kernel ostd osdk
[ "$(git -c safe.directory="$repo" diff --name-only "$base" "$fixed" -- kernel ostd osdk)" = kernel/core/src/time/core/timer.rs ]
git -c safe.directory="$repo" rev-parse HEAD > "$out/harness-head.txt"
cp "$suite/kernel.lock" "$out/original-kernel.lock"
cp "$suite/batch/lock-delta.json" "$out/diagnostic-lock-delta.json"
lscpu > "$out/host-cpu.txt"
free -b > "$out/host-memory.txt"
getconf CLK_TCK > "$out/host-clk-tck.txt"
printf 'PERF_NETDEV=%s\nPERF_CPUS=%s\nVHOST=%s\n' "$PERF_NETDEV" "$PERF_CPUS" "$VHOST" > "$out/transport.txt"
printf 'RELEASE_LTO=%s\nSMP=%s\nMEM=%s\nNETDEV=%s\nPERF_PILOT=%s\n' "${RELEASE_LTO:-0}" "$SMP" "$MEM" "$NETDEV" "$PERF_PILOT" > "$out/build-environment.txt"
cp "$suite/host.lock" test/nixos/Cargo.lock
cargo build --locked --manifest-path test/nixos/Cargo.toml -p test-timer-performance
cmp "$suite/host.lock" test/nixos/Cargo.lock
cp test/nixos/Cargo.lock "$out/host-Cargo.lock"
nix-build --no-out-link "$suite/host.nix" > "$out/host-clients-path"
export PERF_HOST_CLIENTS
PERF_HOST_CLIENTS=$(tail -1 "$out/host-clients-path")
export TAIL_PERF_BIN="$PERF_HOST_CLIENTS/bin/perf"
if [ "${TAIL_PERF:-0}" = 1 ]; then
    "$TAIL_PERF_BIN" --version > "$out/perf-version.txt"
    python3 "$suite/perf_capability.py" --out "$out/perf-capability"
    python3 - "$out/perf-capability/result.json" <<'CHECK_PERF'
import json,sys
result=json.load(open(sys.argv[1]))
assert result.get('events',{}).get('cpu-clock',{}).get('guest_ip_verified'),result
CHECK_PERF
fi
printf 'TAIL_PERF=%s\nTAIL_PERF_BIN=%s\n' "${TAIL_PERF:-0}" "$TAIL_PERF_BIN" >> "$out/build-environment.txt"
# Seed every generated input so OSDK preserves the frozen dependency lock.
base_crate=target/osdk/asterinas-run-base
mkdir -p "$base_crate/src"
cp "$suite/base-crate-Cargo.toml" "$base_crate/Cargo.toml"
sed 's/#TARGET_NAME#/asterinas/g' osdk/src/base_crate/main.rs.template > "$base_crate/src/main.rs"
for arch in x86_64 riscv64 loongarch64 aarch64; do
    cp "osdk/src/base_crate/$arch.ld.template" "$base_crate/$arch.ld"
done
cp "$suite/kernel.lock" "$base_crate/Cargo.lock"
restore_plain() {
    local ref=$1 path
    if [ -f kernel/core/src/batch_probe.rs ]; then
        cmp "$suite/batch/batch_probe.rs" kernel/core/src/batch_probe.rs
        rm kernel/core/src/batch_probe.rs
    fi
    if [ -f osdk/deps/heap-allocator/src/timer_heap_probe.rs ]; then
        cmp "$suite/batch/timer_heap_probe.rs" osdk/deps/heap-allocator/src/timer_heap_probe.rs
        rm osdk/deps/heap-allocator/src/timer_heap_probe.rs
    fi
    for path in kernel/core/src/lib.rs kernel/core/src/syscall/prctl.rs kernel/core/src/time/core/timer.rs osdk/deps/heap-allocator/src/lib.rs osdk/deps/heap-allocator/src/allocator.rs osdk/deps/heap-allocator/Cargo.toml; do
        git -c safe.directory="$repo" show "$ref:$path" > "$path"
    done
}
for variant in base retirement; do
    mkdir -p "$out/$variant"
    ref=$base
    if [ "$variant" = retirement ]; then ref=$fixed; fi
    restore_plain "$ref"
    git -c safe.directory="$repo" diff "$base" -- kernel ostd osdk > "$out/$variant/kernel-source.patch"
    git -c safe.directory="$repo" diff --exit-code "$ref" -- kernel ostd osdk
    sha256sum kernel/core/src/time/core/timer.rs > "$out/$variant/timer-source.sha256"
    if [ "${TAIL_BATCH_BUILD:-0}" = 1 ]; then
        python3 "$suite/batch/check_diagnostic_lock.py" "$suite/kernel.lock" "$suite/batch/kernel-diagnostic.lock"
        python3 "$suite/batch/apply_batch_probe.py" "$repo"
        cp "$suite/batch/kernel-diagnostic.lock" "$base_crate/Cargo.lock"
        git -c safe.directory="$repo" diff "$ref" -- kernel ostd osdk > "$out/$variant/batch-source.patch"
        mkdir -p "$out/$variant/batch-source"
        cp kernel/core/src/{lib.rs,batch_probe.rs,syscall/prctl.rs,time/core/timer.rs} "$out/$variant/batch-source/"
        cp osdk/deps/heap-allocator/src/allocator.rs "$out/$variant/batch-source/heap-allocator.rs"
        cp osdk/deps/heap-allocator/src/lib.rs "$out/$variant/batch-source/heap-lib.rs"
        cp osdk/deps/heap-allocator/src/timer_heap_probe.rs "$out/$variant/batch-source/"
        cp osdk/deps/heap-allocator/Cargo.toml "$out/$variant/batch-source/heap-Cargo.toml"
        sha256sum "$out/$variant/batch-source/"* > "$out/$variant/batch-source.sha256"
    fi
    if [ -d target/osdk/linux-bzimage-setup ]; then
        mv target/osdk/linux-bzimage-setup "target/osdk/linux-bzimage-setup-before-perf-$variant"
    fi
    if [ -f target/nixos/asterinas.img ]; then rm target/nixos/asterinas.img; fi
    make nixos NIXOS_TEST_SUITE=timer-performance NIXOS_DISK_SIZE_IN_MB=8192 > "$out/$variant/build.log" 2>&1
    cp --reflink=auto --sparse=always target/nixos/asterinas.img "$out/$variant/clean.img"
    cp target/osdk/asterinas-run-base/Cargo.lock "$out/$variant/Cargo.lock"
    cp target/osdk/asterinas/asterinas-osdk-bin "$out/$variant/kernel.elf"
    if [ "${TAIL_BATCH_BUILD:-0}" = 1 ]; then
        cmp "$suite/batch/kernel-diagnostic.lock" "$out/$variant/Cargo.lock"
    else
        cmp "$suite/kernel.lock" "$out/$variant/Cargo.lock"
    fi
    sha256sum target/osdk/asterinas/asterinas-osdk-bin target/osdk/iso_root/boot/asterinas-osdk-bin "$out/$variant/clean.img" > "$out/$variant/artifact.sha256"
    echo "PERF_BUILD_READY $variant"
done
cmp "$out/base/Cargo.lock" "$out/retirement/Cargo.lock"
restore_plain "$fixed"
git -c safe.directory="$repo" diff --exit-code "$fixed" -- kernel ostd osdk
python3 "$suite/paired.py"
# This diagnostic campaign does not repeat the previously verified ktest.
