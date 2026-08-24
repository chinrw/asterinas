#!/usr/bin/env bash

# SPDX-License-Identifier: MPL-2.0

set -u

PROBE_MODE=${1:-}
PROBE_RUNS=${2:-5000}

case "$PROBE_MODE" in
    baseline | synchronized) ;;
    *)
        echo "Usage: $0 <baseline|synchronized> [runs]" >&2
        exit 2
        ;;
esac

probe_tmp=$(mktemp -d /tmp/loop-partition-probe.XXXXXX)
probe_disk=""
probe_image=""
probe_partition_one=""
probe_partition_two=""

cleanup_iteration() {
    local cleanup_attempt

    if [ -n "${probe_disk:-}" ]; then
        losetup -d "$probe_disk" 2>/dev/null || true
        probe_disk=""
        for cleanup_attempt in $(seq 1 500); do
            if [ ! -e "$probe_partition_one" ] && [ ! -e "$probe_partition_two" ]; then
                break
            fi
            sleep 0.01
        done
    fi
    if [ -n "${probe_image:-}" ]; then
        rm -f "$probe_image"
        probe_image=""
    fi
}

cleanup_all() {
    cleanup_iteration
    case "$probe_tmp" in
        /tmp/loop-partition-probe.*)
            rmdir "$probe_tmp" 2>/dev/null || true
            ;;
    esac
}

trap cleanup_all EXIT INT TERM

completed=0
immediate_missing=0
waited=0
exact_failure=0
other_failure=0

echo "mode=$PROBE_MODE runs=$PROBE_RUNS"
echo "parted=$(parted --version | head -n 1)"
echo "udevadm=$(command -v udevadm 2>/dev/null || echo absent)"

for iteration in $(seq 1 "$PROBE_RUNS"); do
    probe_image="$probe_tmp/disk-$iteration.img"
    if ! fallocate -l 64M "$probe_image"; then
        echo "iteration=$iteration operation=fallocate failure=setup"
        other_failure=1
        break
    fi
    if ! probe_disk=$(losetup -fP --show "$probe_image"); then
        echo "iteration=$iteration operation=losetup failure=setup"
        other_failure=1
        break
    fi

    probe_partition_one="${probe_disk}p1"
    probe_partition_two="${probe_disk}p2"

    if ! parted "$probe_disk" -- mklabel gpt >/dev/null 2>&1 ||
        ! parted "$probe_disk" -- mkpart ESP fat32 1MB 16MB >/dev/null 2>&1 ||
        ! parted "$probe_disk" -- mkpart root ext2 16MB 100% >/dev/null 2>&1 ||
        ! parted "$probe_disk" -- set 1 esp on >/dev/null 2>&1; then
        echo "iteration=$iteration operation=parted disk=$probe_disk failure=setup"
        other_failure=1
        break
    fi

    if [ ! -b "$probe_partition_one" ] || [ ! -b "$probe_partition_two" ]; then
        immediate_missing=$((immediate_missing + 1))
    fi

    if [ "$PROBE_MODE" = synchronized ]; then
        partprobe "$probe_disk" >/dev/null 2>&1 || true
        wait_attempt=0
        while { [ ! -b "$probe_partition_one" ] || [ ! -b "$probe_partition_two" ]; } &&
            [ "$wait_attempt" -lt 500 ]; do
            sleep 0.01
            wait_attempt=$((wait_attempt + 1))
        done
        if [ "$wait_attempt" -gt 0 ]; then
            waited=$((waited + 1))
        fi
        if [ ! -b "$probe_partition_one" ] || [ ! -b "$probe_partition_two" ]; then
            echo "iteration=$iteration operation=partition-wait disk=$probe_disk failure=timeout"
            other_failure=1
            break
        fi
    fi

    mkfs_error=$(mkfs.fat -F 32 -n boot "$probe_partition_one" 2>&1)
    mkfs_status=$?
    if [ "$mkfs_status" -ne 0 ]; then
        echo "iteration=$iteration operation=mkfs.fat disk=$probe_disk p1=$([ -b "$probe_partition_one" ] && echo present || echo missing)"
        echo "$mkfs_error"
        if [ ! -b "$probe_partition_one" ]; then
            exact_failure=1
        else
            other_failure=1
        fi
        break
    fi

    mkfs_error=$(mkfs.ext2 -L nixos "$probe_partition_two" 2>&1)
    mkfs_status=$?
    if [ "$mkfs_status" -ne 0 ]; then
        echo "iteration=$iteration operation=mkfs.ext2 disk=$probe_disk p2=$([ -b "$probe_partition_two" ] && echo present || echo missing)"
        echo "$mkfs_error"
        if [ ! -b "$probe_partition_two" ]; then
            exact_failure=1
        else
            other_failure=1
        fi
        break
    fi

    completed=$((completed + 1))
    cleanup_iteration
done

echo "mode=$PROBE_MODE completed=$completed immediate_missing=$immediate_missing waited=$waited exact_failure=$exact_failure other_failure=$other_failure"

if [ "$exact_failure" -ne 0 ]; then
    echo "::error::Reproduced the missing loop partition node failure"
    exit 1
fi
if [ "$other_failure" -ne 0 ]; then
    echo "::error::Probe failed for a reason other than the target race"
    exit 2
fi

echo "No missing loop partition node failure observed"
