#!/usr/bin/env bash
set -euo pipefail

for path in /usr/local/qemu "$VDSO_LIBRARY_DIR"; do
    target=$(readlink -f "$path")
    printf '%s -> %s\n' "$path" "$target"
    [[ $target == /nix/store/* && -d $target ]]
done

nix-collect-garbage -d
/usr/local/qemu/bin/qemu-system-x86_64 --version | grep -Fx 'QEMU emulator version 10.2.1'
for arch in x86_64 riscv64 aarch64; do
    test -s "$VDSO_LIBRARY_DIR/vdso_$arch.so"
    sha256sum "$VDSO_LIBRARY_DIR/vdso_$arch.so"
done
printf '%s\n' 'PASS: QEMU and vDSO are available after GC'

make kernel
make run_kernel AUTO_TEST=boot ENABLE_KVM="${PR3982_ENABLE_KVM:-1}"
