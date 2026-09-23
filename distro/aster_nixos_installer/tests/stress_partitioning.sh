#!/usr/bin/env bash

# SPDX-License-Identifier: MPL-2.0

# Regression test for #3700, where udevd's partition-table re-read deleted
# partition devices while the installer was formatting them. The race shows up
# only occasionally, so this script runs the installer's partitioning and
# formatting STRESS_RUNS times. Every run gets a fresh loop disk because the
# installer skips partitioning when partitions already exist.
#
# Exit status 1 means the installer failed, most likely because the race is
# back, and the installer's output is printed. Exit status 2 means the harness
# itself failed, for example because no loop device was available.
#
# It needs root and the host's /dev, as in the development container started
# with `--privileged -v /dev:/dev`, but touches only the loop devices it
# creates.

set -Eeuo pipefail

# Before the fix, the race appeared within 24-240 runs.
STRESS_RUNS=500
MOUNT_SHIM_EXIT=42
DETACH_POLL_ATTEMPTS=500
DETACH_POLL_INTERVAL_SECONDS=0.01
DETACH_ABSENCE_STREAK=5

trap 'echo "::error::Stress harness failed at line $LINENO" >&2; exit 2' ERR

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ASTERINAS_DIR=$(realpath "${SCRIPT_DIR}/../../..")
CONFIG_PATH="${ASTERINAS_DIR}/distro/etc_nixos/configuration.nix"
# The template runs unbuilt. Its @...@ placeholders, which Nix fills in at
# build time, are only used after the first mount, where the shim stops it.
INSTALLER=$(realpath "${SCRIPT_DIR}/../templates/aster-nixos-install")

# State shared by the main loop and the cleanup functions.
stress_tmp=""
stress_disk=""
stress_image=""
stress_detach_attempted=false

main() {
    stress_tmp=$(mktemp -d /tmp/installer-partitioning-stress.XXXXXX)
    trap cleanup_all EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    create_shims

    echo "runs=$STRESS_RUNS installer=$INSTALLER"
    for ((iteration = 1; iteration <= STRESS_RUNS; iteration++)); do
        run_iteration
        if [ $((iteration % 50)) -eq 0 ]; then
            echo "progress completed=$iteration"
        fi
    done
    echo "Partitioning and formatting succeeded in all $STRESS_RUNS runs"
}

create_shims() {
    # The mount shim stops the installer at its first mount, right after
    # formatting, so a run does not pay for a full NixOS installation. Its
    # distinct exit status tells that planned stop apart from a real failure.
    mkdir -p "$stress_tmp/shim"
    cat > "$stress_tmp/shim/mount" <<EOF
#!/bin/sh
exit $MOUNT_SHIM_EXIT
EOF
    chmod +x "$stress_tmp/shim/mount"

    # The mount shim stops the installer before it registers its own cleanup,
    # so point its mktemp into $stress_tmp, which the harness removes on exit.
    cat > "$stress_tmp/shim/mktemp" <<EOF
#!/usr/bin/env bash
exec $(printf '%q -d %q' "$(command -v mktemp)" "$stress_tmp/build.XXXXXX")
EOF
    chmod +x "$stress_tmp/shim/mktemp"
}

run_iteration() {
    local output status=0

    stress_image="$stress_tmp/disk-$iteration.img"
    # The installer's partition layout needs at least 1 GB of disk space.
    # A sparse image provides that capacity while only storing written data.
    if ! truncate -s 1024M "$stress_image"; then
        echo "iteration=$iteration operation=truncate failure=harness"
        exit 2
    fi
    if ! stress_disk=$(losetup -fP --show "$stress_image"); then
        echo "iteration=$iteration operation=losetup failure=harness"
        exit 2
    fi

    output=$(PATH="$stress_tmp/shim:$PATH" \
        bash "$INSTALLER" --config "$CONFIG_PATH" --disk "$stress_disk" 2>&1) || status=$?

    # The marker shows that this run partitioned and formatted the disk instead
    # of finding partitions and skipping straight to the mount.
    if [ "$status" -ne "$MOUNT_SHIM_EXIT" ] ||
        [[ "$output" != *"mkfs finished"* ]]; then
        echo "iteration=$iteration disk=$stress_disk status=$status failure=installer"
        printf '%s\n' "$output"
        exit 1
    fi

    if ! release_iteration; then
        echo "iteration=$iteration operation=loop-cleanup failure=harness"
        exit 2
    fi
}

release_iteration() {
    local absence_streak=0
    local disk=$stress_disk
    local associations
    local _attempt

    if [ -n "$disk" ]; then
        if [ "$stress_detach_attempted" == false ]; then
            # EXIT cleanup calls this again after a failed wait. Detach only
            # once, because by then another process may have attached its own
            # file to the same device.
            stress_detach_attempted=true
            if ! losetup -d "$disk"; then
                echo "iteration=${iteration:-0} disk=$disk image=$stress_image operation=detach failure=harness" >&2
                return 1
            fi
        fi
        # The next run may get the same loop device. If its old partition nodes
        # still exist then, the installer skips partitioning and the run fails
        # without testing anything. So wait until the image has no loop device
        # and the nodes stay absent for several polls in a row, because a
        # partition-table re-read, which udevd can start once the installer's
        # lock is gone, removes them only briefly.
        for ((_attempt = 0; _attempt < DETACH_POLL_ATTEMPTS; _attempt++)); do
            if ! associations=$(losetup --associated "$stress_image"); then
                echo "iteration=${iteration:-0} disk=$disk image=$stress_image operation=query-association failure=harness" >&2
                return 1
            fi
            if [ -z "$associations" ] && [ ! -e "${disk}p1" ] && [ ! -e "${disk}p2" ]; then
                absence_streak=$((absence_streak + 1))
                if [ "$absence_streak" -ge "$DETACH_ABSENCE_STREAK" ]; then
                    break
                fi
            else
                absence_streak=0
            fi
            sleep "$DETACH_POLL_INTERVAL_SECONDS" || return 1
        done
        if [ "$absence_streak" -lt "$DETACH_ABSENCE_STREAK" ]; then
            echo "iteration=${iteration:-0} disk=$disk image=$stress_image operation=wait-detach failure=harness" >&2
            return 1
        fi
        stress_disk=""
        stress_detach_attempted=false
    fi
    rm -f "$stress_image"
}

cleanup_all() {
    local status=$?
    trap - EXIT
    # If the loop device may still be attached, keep the directory so that its
    # backing image is not deleted underneath it.
    if ! release_iteration; then
        echo "::error::Cleanup incomplete: disk=$stress_disk image=$stress_image retained=$stress_tmp" >&2
        if [ "$status" -eq 0 ]; then status=2; fi
    elif ! rm -rf "$stress_tmp"; then
        if [ "$status" -eq 0 ]; then status=2; fi
    fi
    exit "$status"
}

main
