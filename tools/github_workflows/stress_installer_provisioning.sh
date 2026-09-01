#!/usr/bin/env bash

# SPDX-License-Identifier: MPL-2.0

# Fork-only diagnostic: stress the generated aster-nixos-install
# provisioning path (losetup -> parted -> readiness wait -> mkfs).
# A PATH shim replaces mount, so every run aborts right after
# "mkfs finished" and one iteration costs seconds instead of a full
# nixos-install.

set -u

STRESS_RUNS=${1:-500}
MOUNT_SHIM_EXIT=42

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ASTERINAS_DIR=$(realpath "${SCRIPT_DIR}/../..")
CONFIG_PATH="${ASTERINAS_DIR}/distro/etc_nixos/configuration.nix"
KERNEL_STUB="${ASTERINAS_DIR}/target/osdk/iso_root/boot/asterinas-osdk-bin"

stress_tmp=$(mktemp -d /tmp/installer-provisioning-stress.XXXXXX)
stress_disk=""
stress_image=""
partition_one=""
partition_two=""

release_iteration() {
    local absence_streak=0
    local _attempt

    if [ -n "$stress_disk" ]; then
        losetup -d "$stress_disk" 2>/dev/null || true
        stress_disk=""
        # Detach is asynchronous too: wait for stable node absence so the
        # next iteration cannot reuse a loop device that is still dying.
        for _attempt in $(seq 1 500); do
            if [ ! -e "$partition_one" ] && [ ! -e "$partition_two" ]; then
                absence_streak=$((absence_streak + 1))
                if [ "$absence_streak" -ge 5 ]; then
                    break
                fi
            else
                absence_streak=0
            fi
            sleep 0.01
        done
        if [ "$absence_streak" -lt 5 ]; then
            return 1
        fi
    fi
    rm -f "$stress_image"
}

cleanup_all() {
    release_iteration || true
    rm -rf "$stress_tmp"
}
trap cleanup_all EXIT INT TERM

# The installer derivation only symlinks the kernel; provisioning never
# reads it, so a stub keeps the stress independent of a kernel build.
mkdir -p "$(dirname "$KERNEL_STUB")"
[ -e "$KERNEL_STUB" ] || echo stub > "$KERNEL_STUB"
mkdir -p /mnt

pushd "${ASTERINAS_DIR}/distro" >/dev/null || exit 2
nix-build aster_nixos_installer/default.nix \
    --argstr target_platform x86_64-linux \
    --argstr extra-substituters "" \
    --argstr extra-trusted-public-keys "" \
    --out-link "$stress_tmp/installer"
popd >/dev/null || exit 2
INSTALLER="$stress_tmp/installer/bin/aster-nixos-install"
if [ ! -x "$INSTALLER" ]; then
    echo "::error::Installer derivation build failed"
    exit 2
fi

mkdir -p "$stress_tmp/shim"
printf '#!/bin/sh\nexit %s\n' "$MOUNT_SHIM_EXIT" > "$stress_tmp/shim/mount"
chmod +x "$stress_tmp/shim/mount"

completed=0
provisioning_failure=0
harness_failure=0

echo "runs=$STRESS_RUNS installer=$(readlink -f "$INSTALLER")"

for iteration in $(seq 1 "$STRESS_RUNS"); do
    stress_image="$stress_tmp/disk-$iteration.img"
    # Sparse image: the hard-coded partition layout needs >= 1GB of
    # address space but only a few MB are ever written.
    if ! truncate -s 1024M "$stress_image"; then
        echo "iteration=$iteration operation=truncate failure=harness"
        harness_failure=1
        break
    fi
    if ! stress_disk=$(losetup -fP --show "$stress_image"); then
        echo "iteration=$iteration operation=losetup failure=harness"
        harness_failure=1
        break
    fi
    partition_one="${stress_disk}p1"
    partition_two="${stress_disk}p2"

    output=$(PATH="$stress_tmp/shim:$PATH" \
        "$INSTALLER" --config "$CONFIG_PATH" --disk "$stress_disk" 2>&1)
    status=$?

    if [ "$status" -ne "$MOUNT_SHIM_EXIT" ] ||
        ! printf '%s' "$output" | grep -q "mkfs finished"; then
        echo "iteration=$iteration disk=$stress_disk status=$status failure=provisioning"
        printf '%s\n' "$output"
        provisioning_failure=1
        break
    fi

    # Reuse pass: the disk now carries a partition table, so the installer must
    # take the "already partitioned" path under the lock and still reach mount.
    output=$(PATH="$stress_tmp/shim:$PATH" \
        "$INSTALLER" --config "$CONFIG_PATH" --disk "$stress_disk" 2>&1)
    status=$?
    if [ "$status" -ne "$MOUNT_SHIM_EXIT" ] ||
        ! printf '%s' "$output" | grep -q "already partitioned" ||
        printf '%s' "$output" | grep -q "mkfs finished"; then
        echo "iteration=$iteration disk=$stress_disk status=$status failure=reuse"
        printf '%s\n' "$output"
        provisioning_failure=1
        break
    fi

    completed=$((completed + 1))
    if [ $((iteration % 50)) -eq 0 ]; then
        echo "progress completed=$completed"
    fi
    if ! release_iteration; then
        echo "iteration=$iteration operation=loop-cleanup failure=harness"
        harness_failure=1
        break
    fi
done

echo "completed=$completed provisioning_failure=$provisioning_failure harness_failure=$harness_failure"

if [ "$provisioning_failure" -ne 0 ]; then
    echo "::error::Installer provisioning failed under stress"
    exit 1
fi
if [ "$harness_failure" -ne 0 ]; then
    echo "::error::Stress harness failed outside the provisioning path"
    exit 2
fi
echo "Installer provisioning survived $completed runs"
