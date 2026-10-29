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
git -c safe.directory="$repo" diff --exit-code "$fixed" -- kernel ostd
[ "$(git -c safe.directory="$repo" diff --name-only "$base" "$fixed" -- kernel ostd)" = kernel/core/src/time/core/timer.rs ]
git -c safe.directory="$repo" rev-parse HEAD > "$out/harness-head.txt"
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
for variant in base retirement; do
    mkdir -p "$out/$variant"
    ref=$base
    if [ "$variant" = retirement ]; then ref=$fixed; fi
    git -c safe.directory="$repo" show "$ref:kernel/core/src/time/core/timer.rs" > kernel/core/src/time/core/timer.rs
    touch kernel/core/src/time/core/timer.rs
    git -c safe.directory="$repo" diff "$base" -- kernel ostd > "$out/$variant/kernel-source.patch"
    git -c safe.directory="$repo" diff --exit-code "$ref" -- kernel ostd
    sha256sum kernel/core/src/time/core/timer.rs > "$out/$variant/timer-source.sha256"
    if [ -d target/osdk/linux-bzimage-setup ]; then
        mv target/osdk/linux-bzimage-setup "target/osdk/linux-bzimage-setup-before-perf-$variant"
    fi
    if [ -f target/nixos/asterinas.img ]; then rm target/nixos/asterinas.img; fi
    make nixos NIXOS_TEST_SUITE=timer-performance NIXOS_DISK_SIZE_IN_MB=8192 > "$out/$variant/build.log" 2>&1
    cp --reflink=auto --sparse=always target/nixos/asterinas.img "$out/$variant/clean.img"
    cp target/osdk/asterinas-run-base/Cargo.lock "$out/$variant/Cargo.lock"
    cmp "$suite/kernel.lock" "$out/$variant/Cargo.lock"
    sha256sum target/osdk/asterinas/asterinas-osdk-bin target/osdk/iso_root/boot/asterinas-osdk-bin "$out/$variant/clean.img" > "$out/$variant/artifact.sha256"
    echo "PERF_BUILD_READY $variant"
done
cmp "$out/base/Cargo.lock" "$out/retirement/Cargo.lock"
python3 "$suite/paired.py"
if [ "$PERF_PILOT" != 1 ]; then
    mv target/osdk/linux-bzimage-setup target/osdk/linux-bzimage-setup-before-perf-ktest
    (
        cd kernel/core
        CONSOLE=ttyS0 OVMF=on QEMU_DISPLAY=none SMP=1 MEM=4G \
            cargo osdk test timer_lifecycle_regressions \
            --initramfs="$repo/test/initramfs/build/initramfs.cpio.gz" \
            --grub-mkrescue=/usr/bin/grub-mkrescue --grub-boot-protocol=linux \
            --kcmd-args=earlycon --kcmd-args=console=ttyS0 --qemu-args="-accel kvm"
    ) > "$out/targeted-ktest.log" 2>&1
    python3 - "$out/targeted-ktest.log" <<'PY'
from pathlib import Path
import re,sys
text=re.sub(r'\x1b\[[0-9;]*m','',Path(sys.argv[1]).read_text())
assert re.search(r'test aster_core::time::core::timer::tests::timer_lifecycle_regressions \.\.\. ok',text)
assert '1 passed; 0 failed;' in text
PY
fi
