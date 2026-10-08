#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
expected=$(nix --extra-experimental-features 'nix-command flakes' eval \
    --accept-flake-config --no-update-lock-file --raw path:/source#qemu.outPath)
test ! -e "$expected"
if command -v qemu-system-x86_64; then exit 1; fi
printf 'Before build: target QEMU output and executable are absent.\n'
started=$SECONDS
nix --extra-experimental-features 'nix-command flakes' \
    build --accept-flake-config --no-update-lock-file --max-jobs 0 --builders '' \
    --out-link /nix/var/nix/gcroots/qemu path:/source#qemu
printf 'Cache-only build elapsed: %s seconds\n' "$((SECONDS-started))"
test "$(readlink -f /nix/var/nix/gcroots/qemu)" = "$expected"
ln -s /nix/var/nix/gcroots/qemu /usr/local/qemu
nix-store --query --requisites "$expected" > /tmp/qemu-closure-before-gc
printf 'Runtime closure paths: %s\n' "$(wc -l < /tmp/qemu-closure-before-gc)"
# Simulate the downstream removal of all automatic roots in this isolated store.
find /nix/var/nix/gcroots/auto -mindepth 1 -maxdepth 1 -type l -delete
nix-collect-garbage -d
nix-store --check-validity $(cat /tmp/qemu-closure-before-gc)
nix-store --query --roots "$expected"
for target in x86_64 riscv64 loongarch64 aarch64; do
    "/usr/local/qemu/bin/qemu-system-$target" --version | head -1
done
printf '{"execute":"qmp_capabilities"}\n{"execute":"quit"}\n' |
    /usr/local/qemu/bin/qemu-system-x86_64 -machine microvm -accel tcg -display none -nodefaults -S -qmp stdio
printf 'QEMU and its runtime closure survived removal of automatic roots and garbage collection.\n'
