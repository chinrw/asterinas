#!/usr/bin/env bash

# SPDX-License-Identifier: MPL-2.0

# This test repeats disk provisioning to catch the loop-partition readiness
# race in issue #3700. A single successful installation can miss the race, so
# each iteration uses a fresh loop disk and runs the aster-nixos-install
# template through partitioning and formatting.

set -Eeuo pipefail

STRESS_RUNS=500
MOUNT_SHIM_EXIT=42
DETACH_POLL_ATTEMPTS=500
DETACH_POLL_INTERVAL_SECONDS=0.01
DETACH_ABSENCE_STREAK=5

trap 'echo "::error::Stress harness failed at line $LINENO" >&2; exit 2' ERR

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ASTERINAS_DIR=$(realpath "${SCRIPT_DIR}/../..")
CONFIG_PATH="${ASTERINAS_DIR}/distro/etc_nixos/configuration.nix"
# Nix substitutions are only used after the mount shim aborts the installer.
INSTALLER="${ASTERINAS_DIR}/distro/aster_nixos_installer/templates/aster-nixos-install"

stress_tmp=$(mktemp -d /tmp/installer-provisioning-stress.XXXXXX)
stress_disk=""
stress_image=""
stress_detach_attempted=false

release_iteration() {
    local absence_streak=0
    local disk=$stress_disk
    local associations
    local _attempt

    if [ -n "$disk" ]; then
        if [ "$stress_detach_attempted" == false ]; then
            # EXIT cleanup may re-enter this function after a failure. Attempt
            # detach only once because the device number could be reused.
            stress_detach_attempted=true
            if ! losetup -d "$disk"; then
                echo "iteration=${iteration:-0} disk=$disk image=$stress_image operation=detach failure=harness" >&2
                return 1
            fi
        fi
        # Detach is asynchronous, and partition nodes may never have appeared.
        # Confirm that the image is unbound as well as waiting for absent nodes.
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
    if ! release_iteration; then
        echo "::error::Cleanup incomplete: disk=$stress_disk image=$stress_image retained=$stress_tmp" >&2
        if [ "$status" -eq 0 ]; then status=2; fi
    elif ! rm -rf "$stress_tmp"; then
        if [ "$status" -eq 0 ]; then status=2; fi
    fi
    exit "$status"
}
trap cleanup_all EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# The mount shim stops the installer after formatting so each iteration avoids
# the cost of a full NixOS installation.
mkdir -p "$stress_tmp/shim"
cat > "$stress_tmp/shim/mount" <<EOF
#!/bin/sh
exit $MOUNT_SHIM_EXIT
EOF
chmod +x "$stress_tmp/shim/mount"

# The installer exits before registering its cleanup handler. Redirect its
# temporary directory into $stress_tmp so the harness can remove it on exit.
cat > "$stress_tmp/shim/mktemp" <<EOF
#!/usr/bin/env bash
exec $(printf '%q -d %q' "$(command -v mktemp)" "$stress_tmp/build.XXXXXX")
EOF
chmod +x "$stress_tmp/shim/mktemp"

echo "runs=$STRESS_RUNS installer=$(readlink -f "$INSTALLER")"

for ((iteration = 1; iteration <= STRESS_RUNS; iteration++)); do
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

    status=0
    output=$(PATH="$stress_tmp/shim:$PATH" \
        bash "$INSTALLER" --config "$CONFIG_PATH" --disk "$stress_disk" 2>&1) || status=$?

    if [ "$status" -ne "$MOUNT_SHIM_EXIT" ] ||
        [[ "$output" != *"mkfs finished"* ]]; then
        echo "iteration=$iteration disk=$stress_disk status=$status failure=provisioning"
        printf '%s\n' "$output"
        exit 1
    fi

    if ! release_iteration; then
        echo "iteration=$iteration operation=loop-cleanup failure=harness"
        exit 2
    fi
    if [ $((iteration % 50)) -eq 0 ]; then
        echo "progress completed=$iteration"
    fi
done

echo "Installer provisioning survived $STRESS_RUNS runs"
