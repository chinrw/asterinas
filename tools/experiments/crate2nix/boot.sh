#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail

root=$(cd "$(dirname "$0")/../../.." && pwd)
elf=$(realpath "$1")
output=$(realpath -m "$2")
initramfs="$root/test/initramfs/build/initramfs.cpio.gz"
init_script=/test/boot_hello.sh
success_marker='Successfully booted.'
case "${3:-boot}" in
  boot) ;;
  hello-world)
    initramfs=$(nix build .#c2n-exp-integration-initramfs --impure --no-link --print-out-paths)
    init_script=/test/run_c2n_hello.sh
    success_marker=C2N_HELLO_WORLD_SUCCEEDED
    ;;
  *) echo 'Expected boot or hello-world mode' >&2; exit 2 ;;
esac
mkdir -p "$output"
export OVMF=off BOOT_METHOD=vmm-direct BOOT_PROTOCOL=multiboot
export CONSOLE=ttyS0 NETDEV=none QEMU_DISPLAY=none SMP=1 MEM=2G
export CARGO_TARGET_DIR="$output/target"
export C2N_RUSTC_LOG="$output/rustc.jsonl"
export RUSTC_WRAPPER="$root/tools/experiments/crate2nix/record-rustc.py"
: > "$C2N_RUSTC_LOG"
sha256sum "$elf" > "$output/input-before.sha256"
cd "$root/kernel"
timeout 180 "$root/target/c2n-exp-prepare/release/cargo-osdk" osdk run \
  --prebuilt-elf "$elf" --boot-method=vmm-direct --grub-boot-protocol=multiboot2 \
  --initramfs="$initramfs" \
  --init-args="$init_script" \
  --kcmd-args="earlycon console=ttyS0 loglevel=error" --qemu-args="-accel tcg" \
  2>&1 | tee "$output/boot.log"
rg -q -F "$success_marker" "$output/boot.log"
sha256sum "$elf" > "$output/input-after.sha256"
cmp "$output/input-before.sha256" "$output/input-after.sha256"
python3 - "$C2N_RUSTC_LOG" <<'PY'
import json
import sys

for line in open(sys.argv[1]):
    invocation = json.loads(line)
    if "--crate-name" in invocation["argv"]:
        raise SystemExit("Boot packaging unexpectedly compiled a crate")
PY
