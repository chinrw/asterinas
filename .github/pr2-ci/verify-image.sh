#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
qemu=$(readlink -f /usr/local/qemu)
test "$qemu" = "$(readlink -f /nix/var/nix/gcroots/qemu)"
nix-store --query --requisites "$qemu" > /tmp/qemu-runtime-paths
nix-store --check-validity $(cat /tmp/qemu-runtime-paths)
nix-store --query --roots "$qemu"
for arch in x86_64 riscv64 loongarch64 aarch64; do
    "/usr/local/qemu/bin/qemu-system-$arch" --version | head -1
done
if [ -n "${VDSO_LIBRARY_DIR:-}" ]; then
    test -L "$VDSO_LIBRARY_DIR"
    (cd "$VDSO_LIBRARY_DIR" && sha256sum vdso_*.so)
fi
if [ "${1:-}" = gc ]; then
    nix-collect-garbage -d
    nix-store --check-validity $(cat /tmp/qemu-runtime-paths)
    /usr/local/qemu/bin/qemu-system-x86_64 --version
    if [ -n "${VDSO_LIBRARY_DIR:-}" ]; then
        for arch in x86_64 riscv64 aarch64; do
            test -s "$VDSO_LIBRARY_DIR/vdso_${arch}.so"
        done
    fi
fi
