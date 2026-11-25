#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -eu

export PATH=/bin:/sbin:/usr/bin:/usr/sbin
export REGRESSION_BLOCK_DEVICES=virtio

mount -t proc proc /proc
mount -t sysfs sysfs /sys
grep -q '^[^ ]* /dev devtmpfs ' /proc/mounts || mount -t devtmpfs devtmpfs /dev

# Keep test output on ttyS0 while kernel printk goes to ttyS1.
exec < /dev/ttyS0 > /dev/ttyS0 2>&1

finish() {
    trap - EXIT HUP INT TERM
    set +e
    # Drain queued serial output before poweroff terminates the guest.
    stty "$(stty -g < /dev/ttyS0)" < /dev/ttyS0
    sync
    poweroff -f
    while :; do sleep 3600; done
}
trap finish EXIT HUP INT TERM

mkdir -p /dev/pts /dev/shm
mount -t devpts devpts /dev/pts
chmod 0666 /dev/pts/ptmx
ln -sf pts/ptmx /dev/ptmx
mount -t tmpfs -o mode=1777 tmpfs /dev/shm
mount -t tmpfs -o mode=1777 tmpfs /tmp
mount -t configfs configfs /sys/kernel/config
mount -t cgroup2 cgroup2 /sys/fs/cgroup

# Wait up to ~10 seconds (100 * 0.1s) for each block device node to appear.
max_wait_polls=100
for dev in /dev/vda /dev/vdb /dev/vdc /dev/nvme0n1; do
    poll=0
    while [ ! -b "$dev" ] && [ "$poll" -lt "$max_wait_polls" ]; do
        sleep 0.1
        poll=$((poll + 1))
    done
    [ -b "$dev" ] || { echo "Missing block device: $dev" >&2; exit 1; }
done

mount -t ext2 /dev/vda /ext2
mount -t exfat /dev/vdb /exfat
ip link set lo up
# Keep eth0 up without an IPv4 address because netlink_route expects only loopback addresses.
ip link set eth0 up

# Allocate a controlling terminal for the regression session, matching `src/init`.
script /dev/null -q -c "/test/run_regression_test.sh && echo LINUX_REGRESSION_PASSED"
