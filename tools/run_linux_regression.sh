#!/usr/bin/env bash

# SPDX-License-Identifier: MPL-2.0

set -euo pipefail

ROOT_DIR="$(CDPATH='' cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

timeout_secs=900
output_dir=""

usage() {
    cat <<EOF
Usage: $(basename "$0") [--timeout <sec>] [--output <dir>]

Run the in-house regression suite in a single x86_64 Linux KVM QEMU VM using Nix.
Requires read/write access to /dev/kvm.

Options:
  --timeout <sec>  Overall VM timeout in seconds (default: 900)
  --output <dir>   Save console.log and kernel.log in <dir>
  -h, --help       Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --timeout|--output)
            [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
            case "$1" in
                --timeout) timeout_secs="$2" ;;
                --output) output_dir="$2" ;;
            esac
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ -r /dev/kvm && -w /dev/kvm ]] || { echo "KVM is required (/dev/kvm must be readable and writable)." >&2; exit 1; }
[[ "$timeout_secs" =~ ^[1-9][0-9]*$ ]] || { echo "Invalid --timeout: $timeout_secs" >&2; exit 2; }

if [[ -n "$output_dir" ]]; then
    case "$output_dir" in
        -*) output_dir="./$output_dir" ;;
    esac
fi

# Forward `flake.nix` binary caches so `nix-build` uses Cachix outside `nix develop`.
if command -v nix-instantiate >/dev/null 2>&1 && [[ -f "$ROOT_DIR/flake.nix" ]]; then
    for key in extra-substituters extra-trusted-public-keys; do
        val=$(CDPATH='' cd -- "$ROOT_DIR" && nix-instantiate --eval --strict \
            -E "builtins.concatStringsSep \" \" ((import ./flake.nix).nixConfig.\"$key\" or [])" 2>/dev/null || true)
        if [[ "$val" == \"*\" ]]; then
            val="${val#\"}"
            val="${val%\"}"
            if [[ -n "$val" && "$val" != *$'\n'* && "$val" != *\\* ]]; then
                export NIX_CONFIG="${NIX_CONFIG:+$NIX_CONFIG$'\n'}$key = $val"
            fi
        fi
    done
fi

scratch=$(mktemp -d)
log_dir="$scratch/logs"
mkdir -p -- "$log_dir"
console_log="$log_dir/console.log"
kernel_log="$log_dir/kernel.log"

export_logs() {
    [[ -n "$output_dir" ]] || return 0
    if [[ -f "$console_log" ]]; then
        cp -f -- "$console_log" "$output_dir/console.log"
    fi
    if [[ -f "$kernel_log" ]]; then
        cp -f -- "$kernel_log" "$output_dir/kernel.log"
    fi
}

trap 'rm -rf -- "$scratch"' EXIT
trap 'export_logs || true; exit 130' INT
trap 'export_logs || true; exit 143' TERM

if [[ -n "$output_dir" ]]; then
    mkdir -p -- "$output_dir"
    rm -f -- "$output_dir/console.log" "$output_dir/kernel.log"
fi

bundle="$scratch/bundle"
nix-build "$ROOT_DIR/test/initramfs/nix/linux-regression.nix" --out-link "$bundle" >/dev/null
export PATH="$bundle/host-tools/bin:$PATH"

disks="$scratch/disks"
mkdir -p -- "$disks"
truncate -s 2G "$disks/ext2.img"
mke2fs -q -t ext2 -F "$disks/ext2.img"
truncate -s 512M "$disks/exfat.img"
mkfs.exfat "$disks/exfat.img" >/dev/null
truncate -s 512M "$disks/aux.img"
mke2fs -q -t ext2 -F "$disks/aux.img"
truncate -s 2G "$disks/nvme.img"
mke2fs -q -t ext2 -F "$disks/nvme.img"
disks_qemu="${disks//,/,,}"

# PCI slots (`0x1` VGA, `0x4` NVMe, `0x6`-`0x8` virtio-blk) match `tools/qemu_args.sh`
# and `test/initramfs/src/regression/fs/sysfs/device/pci.c`.
set +e
timeout --foreground --kill-after=10 "$timeout_secs" \
    qemu-system-x86_64 \
    -machine q35 -accel kvm -cpu host -smp 4 -m 2048 \
    -nodefaults -no-reboot -display none -monitor none \
    -device VGA,bus=pcie.0,addr=0x1 \
    -kernel "$bundle/bzImage" \
    -initrd "$bundle/initramfs.cpio.gz" \
    -append "console=ttyS1 rdinit=/linux-init net.ifnames=0 panic=-1" \
    -serial stdio \
    -serial "file:$kernel_log" \
    -drive "if=none,format=raw,cache=unsafe,id=ext2,file=$disks_qemu/ext2.img" \
    -device virtio-blk-pci,bus=pcie.0,addr=0x6,drive=ext2 \
    -drive "if=none,format=raw,cache=unsafe,id=exfat,file=$disks_qemu/exfat.img" \
    -device virtio-blk-pci,bus=pcie.0,addr=0x7,drive=exfat \
    -drive "if=none,format=raw,cache=unsafe,id=aux,file=$disks_qemu/aux.img" \
    -device virtio-blk-pci,bus=pcie.0,addr=0x8,drive=aux \
    -drive "if=none,format=raw,cache=unsafe,id=nvme,file=$disks_qemu/nvme.img" \
    -device nvme,bus=pcie.0,addr=0x4,serial=regression,drive=nvme \
    -netdev user,id=net0 -device virtio-net-pci,netdev=net0 \
    -object rng-random,id=rng0,filename=/dev/urandom \
    -device virtio-rng-pci,rng=rng0 \
    < /dev/null 2>&1 | tee -- "$console_log"
pipe_rc=("${PIPESTATUS[@]}")
set -e
qemu_rc=${pipe_rc[0]}
tee_rc=${pipe_rc[1]}

if [[ "$qemu_rc" -eq 0 && "$tee_rc" -eq 0 ]] && tr -d '\r' < "$console_log" | grep -qx 'LINUX_REGRESSION_PASSED'; then
    export_logs
    exit 0
fi

export_logs || true
echo "Linux regression failed (QEMU exit code: $qemu_rc, tee exit code: $tee_rc)." >&2
if [[ "$qemu_rc" -eq 124 ]]; then
    echo "VM timed out after ${timeout_secs}s." >&2
fi
if [[ -f "$kernel_log" ]]; then
    echo "--- Last 40 lines of kernel.log ---" >&2
    tail -n 40 -- "$kernel_log" >&2
fi
exit 1
