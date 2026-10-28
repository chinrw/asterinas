#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
[ "${TIMER_BENCHMARK_EPHEMERAL:-}" = 1 ]
suite=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$suite/../../../.." && pwd)
cd "$repo"
git -c safe.directory="$repo" diff --quiet
git -c safe.directory="$repo" diff --cached --quiet
base=449721d9ce02adfe7c6d2a618e044b218867615b
fixed=cba4d13d80f45828be80138f403db758d0ef68b5
git -c safe.directory="$repo" fetch --no-tags --depth=1 origin "$base"
git -c safe.directory="$repo" fetch --no-tags --depth=1 origin "$fixed"
out="$repo/target/notingo-native-controls/fresh-benchmark"
mkdir -p "$out"
git -c safe.directory="$repo" rev-parse HEAD > "$out/harness-head.txt"
lscpu > "$out/host-cpu.txt"
free -b > "$out/host-memory.txt"
cargo build --manifest-path test/nixos/Cargo.toml -p test-development-tools-go
cp test/nixos/Cargo.lock "$out/host-Cargo.lock"
python3 "$suite/prepare_fresh_helpers.py"
for variant in base retirement; do
    mkdir -p "$out/$variant"
    ref=$base
    if [ "$variant" = retirement ]; then ref=$fixed; fi
    git -c safe.directory="$repo" show "$ref:kernel/core/src/time/core/timer.rs" > kernel/core/src/time/core/timer.rs
    touch kernel/core/src/time/core/timer.rs
    git -c safe.directory="$repo" diff "$base" -- kernel > "$out/$variant/kernel-source.patch"
    sha256sum kernel/core/src/time/core/timer.rs > "$out/$variant/timer-source.sha256"
    if [ -d target/osdk/linux-bzimage-setup ]; then
        mv target/osdk/linux-bzimage-setup "target/osdk/linux-bzimage-setup-before-fresh-$variant"
    fi
    if [ -f target/nixos/asterinas.img ]; then rm target/nixos/asterinas.img; fi
    make nixos NIXOS_TEST_SUITE=development-tools-go NIXOS_DISK_SIZE_IN_MB=8192 > "$out/$variant/build.log" 2>&1
    cp --reflink=auto --sparse=always target/nixos/asterinas.img "$out/$variant/clean.img"
    cp target/osdk/asterinas-run-base/Cargo.lock "$out/$variant/Cargo.lock"
    sha256sum target/osdk/asterinas/asterinas-osdk-bin target/osdk/iso_root/boot/asterinas-osdk-bin "$out/$variant/clean.img" > "$out/$variant/artifact.sha256"
    echo "FRESH_BUILD_READY $variant"
done
cmp "$out/base/Cargo.lock" "$out/retirement/Cargo.lock"
python3 "$suite/fresh_benchmark.py"
