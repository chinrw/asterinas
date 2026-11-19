#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -eu

export PATH=/bin:/sbin:/usr/bin:/usr/sbin
export REGRESSION_TEST_PLATFORM=linux
export REGRESSION_BUILTIN_CONFIG=/etc/benchmark-linux.config
export REGRESSION_BLOCK_DEVICES=virtio
REGRESSION_LINUX_MODULE_DIR="/lib/modules/$(uname -r)"
export REGRESSION_LINUX_MODULE_DIR

group=setup
case_number=0
batch=
case_timeout=180

finish() {
    status=$?
    trap - EXIT HUP INT TERM
    set +e
    printf '\nREGRESSION_CONSOLE_END %s %s\n' "$group" "$case_number" > /dev/ttyS0
    # BusyBox stty uses TCSADRAIN when restoring settings. Wait for the UART
    # to transmit the last assertions before poweroff discards queued output.
    stty "$(stty -g < /dev/ttyS0)" < /dev/ttyS0 || status=1
    printf 'LINUX_REGRESSION_RESULT %s %s %s\n' "$group" "$case_number" "$status" > /dev/ttyS1
    stty "$(stty -g < /dev/ttyS1)" < /dev/ttyS1
    sync
    poweroff -f
    # PID 1 must remain alive if the poweroff syscall unexpectedly returns.
    while :; do sleep 3600; done
}

trap finish EXIT
trap 'exit 1' HUP INT TERM

mount -t proc proc /proc
# The mmap and exec low-address tests require page one to be below the floor.
# Use the same 64 KiB boundary as Asterinas.
echo 65536 > /proc/sys/vm/mmap_min_addr
for option in $(cat /proc/cmdline); do
    case "$option" in
        regression_group=*) group=${option#regression_group=} ;;
        regression_batch=*) batch=${option#regression_batch=} ;;
        regression_timeout=*) case_timeout=${option#regression_timeout=} ;;
        regression_case=*) case_number=${option#regression_case=} ;;
    esac
done
case "$group" in
    setup|''|[!a-z]*|*[!a-z0-9_]*)
        echo "Missing or invalid regression_group kernel argument." >&2
        exit 2
        ;;
esac
case "$case_number" in
    ''|0*|*[!0-9]*)
        echo "Missing or invalid regression_case kernel argument." >&2
        exit 2
        ;;
esac
export REGRESSION_TEST_CASE=$case_number

mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
exec < /dev/console > /dev/console 2>&1
mkdir -p /dev/pts /dev/shm
mount -t devpts devpts /dev/pts
chmod 0666 /dev/pts/ptmx
ln -sf pts/ptmx /dev/ptmx
mount -t tmpfs -o mode=1777 tmpfs /dev/shm
mount -t tmpfs -o mode=1777 tmpfs /tmp
chmod 0755 /
mkdir -p /ramfs

mount -t securityfs securityfs /sys/kernel/security
mount -t configfs configfs /sys/kernel/config
mount -t cgroup2 cgroup2 /sys/fs/cgroup

for device in /dev/vda /dev/vdb /dev/vdc /dev/nvme0n1; do
    attempt=0
    while [ ! -b "$device" ] && [ "$attempt" -lt 100 ]; do
        sleep 0.1
        attempt=$((attempt + 1))
    done
    if [ ! -b "$device" ]; then
        echo "Missing regression block device: $device" >&2
        exit 1
    fi
done
mount -t ext2 /dev/vda /ext2
# Keep this path absent until the exFAT fixture has mounted its test disk.
rmdir /exfat

ip link set lo up
ip link set eth0 up
ip address add 10.0.2.15/24 dev eth0
ip route add default via 10.0.2.2

uname -a
cat /proc/cmdline
cat /proc/mounts

# Give the workload its own controlling PTY. Releasing a session that owns
# ttyS0 can flush pending console output before the host receives it.
if [ -z "$batch" ]; then
    /usr/bin/script --quiet --return --command "/bin/sh /test/run_regression_test.sh $group" /dev/null < /dev/null
    exit $?
fi

case "$batch" in *[!0-9,]*|,*|*,|*,,*) exit 2 ;; esac
case "$case_timeout" in ''|0*|*[!0-9]*) exit 2 ;; esac
group_failed=0
for number in $(echo "$batch" | tr , ' '); do
    export REGRESSION_TEST_CASE=$number
    read -r begin _ < /proc/uptime
    printf '\nBATCH_BEGIN %s %s %s\n' "$group" "$number" "$begin"
    printf 'BATCH_BEGIN %s %s %s\n' "$group" "$number" "$begin" > /dev/ttyS1
    rc=0
    /bin/busybox timeout -k 5 "$case_timeout" /usr/bin/script --quiet --return \
        --command "/bin/sh /test/run_regression_test.sh $group" /dev/null < /dev/null || rc=$?
    read -r end _ < /proc/uptime
    printf '\nBATCH_END %s %s %s %s\n' "$group" "$number" "$rc" "$end"
    printf 'BATCH_END %s %s %s %s\n' "$group" "$number" "$rc" "$end" > /dev/ttyS1
    if [ "$rc" -ne 0 ]; then group_failed=1; fi
done
exit "$group_failed"
