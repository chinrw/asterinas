#!/bin/bash
# SPDX-License-Identifier: MPL-2.0

# Run only in a disposable candidate container, without network access.
set -euxo pipefail
export LC_ALL=C
qemu=$1
qemu_version=$2
grub=$3
dev_tools=$4
ovmf_sizes=$5
vdso=$6
image=$7
gc=$8
nixpkgs_url=$9

test "$(readlink -f /usr/local/qemu)" = "$qemu"
test "$(readlink -f /usr/local/grub)" = "$grub"
test "$(readlink -f /nix/var/nix/gcroots/qemu)" = "$qemu"
test "$(readlink -f /nix/var/nix/gcroots/grub)" = "$grub"
test "$(readlink -f /nix/var/nix/gcroots/dev-tools)" = "$dev_tools"
test ! -e /nix/var/nix/gcroots/ovmf
outputs=("$qemu" "$grub" "$dev_tools")
if [[ "$image" == kernel-dev || "$image" == dev ]]; then
    test "$VDSO_LIBRARY_DIR" = /root/linux_vdso
    test "$(readlink -f "$VDSO_LIBRARY_DIR")" = "$vdso"
    outputs+=("$vdso")
fi
nix-store -qR "${outputs[@]}" > /tmp/runtime-paths
mapfile -t runtime_paths < /tmp/runtime-paths
nix-store -q --roots "${outputs[@]}"

if [[ "$gc" == yes ]]; then
    printf 'PR5 unrooted GC probe\n' > /tmp/pr5-gc-probe
    unrooted=$(nix-store --add /tmp/pr5-gc-probe)
    # The prebuilt image already removes automatic roots during its build.
    if [[ "$image" == osdk-dev ]]; then
        rm -f /nix/var/nix/gcroots/auto/*
    fi
    nix-collect-garbage -d
    test ! -e "$unrooted"
fi
nix-store --check-validity "${runtime_paths[@]}"

case "$(uname -m)" in
    x86_64) machine="Advanced Micro Devices X86-64" ;;
    aarch64) machine=AArch64 ;;
    *) exit 1 ;;
esac
for arch in x86_64 riscv64 loongarch64 aarch64; do
    program="qemu-system-$arch"
    test "$(readlink -f "$(command -v "$program")")" = "$qemu/bin/$program"
    "$program" --version > /tmp/qemu-version
    test "$(head -n 1 /tmp/qemu-version)" = "QEMU emulator version $qemu_version"
    readelf -h "$qemu/bin/$program" | grep -F "$machine"
done

test "$(command -v grub-mkrescue)" = /usr/local/grub/bin/grub-mkrescue
grub-mkrescue --version
grub-mkfont --version
native_grub=$(python3 - "$grub/bin/grub-mkrescue" <<'PY'
import pathlib
import shlex
import sys

path = pathlib.Path(sys.argv[1]).resolve()
if path.read_bytes()[:4] != b"\x7fELF":
    # makeWrapper emits one exec statement that names the native executable.
    commands = [shlex.split(line) for line in path.read_text().splitlines()
                if line.startswith("exec ")]
    assert len(commands) == 1, path
    command = commands[0][1:]
    if command[0] == "-a":
        command = command[2:]
    path = pathlib.Path(command[0]).resolve()
assert path.read_bytes()[:4] == b"\x7fELF", path
print(path)
PY
)
readelf -h "$native_grub" | grep -F "$machine"
test -s "$grub/lib/grub/x86_64-efi/linux.mod"
readelf -h "$grub/lib/grub/x86_64-efi/linux.mod" | grep -F 'Advanced Micro Devices X86-64'
test -s /usr/local/grub/share/grub/unicode.pf2
test -r /usr/local/grub/share/grub/unicode.pf2
sha256sum /usr/local/grub/share/grub/unicode.pf2 /usr/share/grub/unicode.pf2

# EFI handover must continue to use the distribution's separate GRUB.
test "$(readlink -f /usr/bin/grub-mkrescue)" != "$native_grub"
/usr/bin/grub-mkrescue --version
readelf -h /usr/bin/grub-mkrescue | grep -F "$machine"
dpkg-query -W grub-efi-amd64 grub2-common

mkdir -p /tmp/pr5-iso-root/boot/grub
printf 'set timeout=0\nmenuentry "PR5 image probe" { halt; }\n' > /tmp/pr5-iso-root/boot/grub/grub.cfg
for provider in project distro; do
    if [[ "$provider" == project ]]; then
        mkrescue="grub-mkrescue"
    else
        mkrescue=/usr/bin/grub-mkrescue
    fi
    # Do not pass --directory: this must exercise the installed default.
    "$mkrescue" -o "/tmp/pr5-$provider.iso" /tmp/pr5-iso-root
    xorriso -indev "/tmp/pr5-$provider.iso" -report_el_torito plain 2>&1 \
        | tee "/tmp/pr5-$provider-el-torito.log"
    grep -F UEFI "/tmp/pr5-$provider-el-torito.log"
    sha256sum "/tmp/pr5-$provider.iso"
done

python3 - "$ovmf_sizes" <<'PY'
import hashlib
import json
import sys
for name, expected in json.loads(sys.argv[1]).items():
    data = open(f"/root/ovmf/release/{name}", "rb").read()
    assert expected > 0 and len(data) == expected, (name, len(data), expected)
    print(f"{name} {len(data)} {hashlib.sha256(data).hexdigest()}")
PY

for tool in cargo-objcopy cargo-udeps mdbook mdbook-mermaid typos; do
    test "$(readlink -f "$(command -v "$tool")")" = "$(readlink -f "$dev_tools/bin/$tool")"
    test ! -e "/root/.cargo/bin/$tool"
done
typos --version | grep -Fx 'typos-cli 1.39.0'
mdbook --version
mdbook-mermaid --version
cargo udeps --help > /dev/null
python3 - <<'PY'
import os
import pathlib
import stat

path = pathlib.Path("/root/ovmf/release/OVMF_VARS.fd")
assert not path.is_symlink()
assert not str(path.resolve()).startswith("/nix/store/")
assert path.stat().st_mode & stat.S_IWUSR
with path.open("r+b") as f:
    first_byte = f.read(1)
    f.seek(0)
    assert f.write(first_byte) == 1
    f.flush()
    os.fsync(f.fileno())
print("OVMF_VARS.fd is a writable copy outside the Nix store")
PY
if [[ "$image" == kernel-dev || "$image" == dev ]]; then
    for arch in x86_64 riscv64 aarch64; do
        test -s "$VDSO_LIBRARY_DIR/vdso_$arch.so"
    done
fi
if [[ "$image" != osdk-dev ]]; then
    nix-channel --list > /tmp/channels
    grep -Fx "nixpkgs $nixpkgs_url" /tmp/channels
    grep -Fx "nixos $nixpkgs_url" /tmp/channels
fi
echo "Image checks passed: $image (GC: $gc)"
