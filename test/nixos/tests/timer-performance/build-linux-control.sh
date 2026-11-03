#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
[ "${TIMER_BENCHMARK_EPHEMERAL:-}" = 1 ]
[ "${RELEASE_LTO:-}" = 1 ]
[ "${TAIL_BATCH_BUILD:-}" = 0 ]
[ "${TAIL_PERF:-}" = 0 ]
suite=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$suite/../../../.." && pwd)
cd "$repo"
base=e0d8b839f95f608c8a7e9c8942e01614a3b3b260
fixed=64848d3e0a23ff0903f4be485fb7750c1709a575
out="$repo/target/timer-linux-control"
[ ! -e "$out" ]
[ ! -e target/osdk/linux-bzimage-setup ]
[ ! -e target/nixos/asterinas.img ]
case "$(git -c safe.directory="$repo" remote get-url origin)" in
    https://github.com/chinrw/asterinas|https://github.com/chinrw/asterinas.git|git@github.com:chinrw/asterinas.git) ;;
    *) echo 'The diagnostic builder requires the owned fork origin.' >&2; exit 1 ;;
esac
git -c safe.directory="$repo" fetch --no-tags --depth=1 origin "$base"
git -c safe.directory="$repo" fetch --no-tags --depth=1 origin "$fixed"
git -c safe.directory="$repo" diff --exit-code "$fixed" -- kernel ostd osdk
[ "$(git -c safe.directory="$repo" diff --name-only "$base" "$fixed" -- kernel ostd osdk)" = kernel/core/src/time/core/timer.rs ]
[ ! -e kernel/core/src/batch_probe.rs ]
[ ! -e osdk/deps/heap-allocator/src/timer_heap_probe.rs ]
mkdir -p "$out"
mkdir "$out/retirement"
printf '%s  %s\n' e8588a8b8146949e286f1f9ae5636685b82f5ed2ec0721fe9194d226ff23b750 "$suite/kernel.lock" | sha256sum -c -
cp "$suite/kernel.lock" "$out/original-kernel.lock"
mkdir "$out/protocol-source"
for name in run_history_control.py paired_history_control.py qemu_select.py guest_network_control.py history_client.py linux-boot.nix build-linux-control.sh host_clients.py tail_observe.py trace_validation.py validation.py packet_coverage.py qmp_snapshot.py owned_qemu.py loadcheck.py timer_helper.nix host.nix; do
    cp "$suite/$name" "$out/protocol-source/$name"
done
cp "$suite/src/main.rs" "$out/protocol-source/main.rs"
cp distro/nixpkgs.nix "$out/protocol-source/nixpkgs.nix"
sha256sum "$out/protocol-source/"* > "$out/protocol-source.sha256"
git -c safe.directory="$repo" rev-parse HEAD > "$out/harness-head.txt"
git -c safe.directory="$repo" diff "$base" -- kernel ostd osdk > "$out/retirement/kernel-source.patch"
sha256sum kernel/core/src/time/core/timer.rs > "$out/retirement/timer-source.sha256"
lscpu > "$out/host-cpu.txt"
free -b > "$out/host-memory.txt"
getconf CLK_TCK > "$out/host-clk-tck.txt"
qemu-system-x86_64 --version > "$out/qemu-version.txt"
grep -Fx 'QEMU emulator version 10.2.1' "$out/qemu-version.txt"
sha256sum "$(command -v qemu-system-x86_64)" /root/ovmf/release/OVMF.fd > "$out/qemu-firmware.sha256"
printf 'PERF_NETDEV=%s\nPERF_CPUS=%s\nVHOST=%s\n' "$PERF_NETDEV" "$PERF_CPUS" "$VHOST" > "$out/transport.txt"
printf 'RELEASE_LTO=%s\nSMP=%s\nMEM=%s\nNETDEV=%s\nPERF_PILOT=%s\nOVMF=%s\nMAKEFLAGS=%s\nTAIL_PERF=%s\nTAIL_BATCH_BUILD=%s\n' "$RELEASE_LTO" "$SMP" "$MEM" "$NETDEV" "$PERF_PILOT" "$OVMF" "$MAKEFLAGS" "$TAIL_PERF" "$TAIL_BATCH_BUILD" > "$out/build-environment.txt"
cp "$suite/host.lock" test/nixos/Cargo.lock
cargo build --locked --manifest-path test/nixos/Cargo.toml -p test-timer-performance
cmp "$suite/host.lock" test/nixos/Cargo.lock
cp test/nixos/Cargo.lock "$out/host-Cargo.lock"
nix-build --no-out-link "$suite/host.nix" > "$out/host-clients-path"
nix-build --no-out-link "$suite/linux-boot.nix" --arg source "$repo" > "$out/linux-boot-path"
linux_boot=$(tail -1 "$out/linux-boot-path")
cp "$linux_boot/kernel" "$out/linux-kernel.bzImage"
cp "$linux_boot/initrd" "$out/linux-initrd.gz"
sha256sum "$out/linux-kernel.bzImage" "$out/linux-initrd.gz" > "$out/linux-boot.sha256"
# Preserve the same dependency lock while OSDK generates its base crate.
base_crate=target/osdk/asterinas-run-base
mkdir -p "$base_crate/src"
cp "$suite/base-crate-Cargo.toml" "$base_crate/Cargo.toml"
sed 's/#TARGET_NAME#/asterinas/g' osdk/src/base_crate/main.rs.template > "$base_crate/src/main.rs"
for arch in x86_64 riscv64 loongarch64 aarch64; do
    cp "osdk/src/base_crate/$arch.ld.template" "$base_crate/$arch.ld"
done
cp "$suite/kernel.lock" "$base_crate/Cargo.lock"
# One plain candidate build supplies the identical userland bytes for both OSes.
make nixos NIXOS_TEST_SUITE=timer-performance NIXOS_DISK_SIZE_IN_MB=8192 > "$out/retirement/build.log" 2>&1
cp --reflink=auto --sparse=always target/nixos/asterinas.img "$out/retirement/clean.img"
cp target/osdk/asterinas/asterinas-osdk-bin "$out/retirement/kernel.elf"
cp target/osdk/iso_root/boot/asterinas-osdk-bin "$out/retirement/boot-kernel.bin"
cp "$base_crate/Cargo.lock" "$out/retirement/Cargo.lock"
cmp "$suite/kernel.lock" "$out/retirement/Cargo.lock"
git -c safe.directory="$repo" diff --exit-code "$fixed" -- kernel ostd osdk
sha256sum "$out/retirement/kernel.elf" "$out/retirement/boot-kernel.bin" "$out/retirement/clean.img" > "$out/retirement/artifact.sha256"
helper_expr='import ./test/nixos/tests/timer-performance/timer_helper.nix { pkgs=import ./distro/nixpkgs.nix {}; }'
helper=$(nix-build --no-out-link --expr "$helper_expr")
sha256sum "$helper/bin/timer-perf" | cut -d ' ' -f1 > "$out/helper-sha256.txt"
python3 - "$out" "$base" "$fixed" "$linux_boot" <<'METADATA'
import hashlib,json,subprocess,sys
from pathlib import Path
out,base,fixed,boot=Path(sys.argv[1]),sys.argv[2],sys.argv[3],Path(sys.argv[4])
def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
metadata={'base':base,'candidate':fixed,'plain_source_matches_candidate':True,
    'batch_injection':False,'perf_enabled':False,'release_lto':True,
    'kernel_lock_sha256':sha(out/'original-kernel.lock'),
    'produced_kernel_lock_sha256':sha(out/'retirement/Cargo.lock'),
    'component_git_trees':{name:subprocess.check_output(['git','-c','safe.directory='+str(Path.cwd()),'rev-parse',fixed+':'+name],text=True).strip() for name in ['kernel','ostd','osdk']},
    'timer_source_sha256':sha(Path('kernel/core/src/time/core/timer.rs')),
    'nixpkgs_pin_source_sha256':sha(Path('distro/nixpkgs.nix')),
    'linux_boot_bundle':str(boot),'linux_kernel_version':(boot/'kernel-version').read_text().strip(),
    'nixpkgs_path':(boot/'nixpkgs-path').read_text().strip(),
    'asterinas_elf_sha256':sha(out/'retirement/kernel.elf'),
    'asterinas_boot_image_sha256':sha(out/'retirement/boot-kernel.bin'),
    'clean_image_sha256':sha(out/'retirement/clean.img'),
    'linux_kernel_sha256':sha(out/'linux-kernel.bzImage'),'linux_initrd_sha256':sha(out/'linux-initrd.gz')}
(out/'build-metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
METADATA
python3 "$suite/run_history_control.py"
