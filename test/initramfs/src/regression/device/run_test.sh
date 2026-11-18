#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

. "${REGRESSION_TEST_ROOT:-/test}/run_test_case.sh"

for test in drm/device_node drm/gem_create_dumb_buffer drm/get_info \
    drm/kms_properties drm/kms_resources drm/master; do
    if [ "${REGRESSION_TEST_PLATFORM:-asterinas}" = linux ]; then
        skip_test_case "The Linux regression VM has no DRM device." "./$test"
    else
        run_test_case "./$test"
    fi
done

run_test_case ./pty/close_pty
run_test_case ./pty/open_ptmx
run_test_case ./pty/open_pty
run_test_case ./pty/open_pty_peer
run_test_case ./pty/pty_blocking
run_test_case ./pty/pty_packet_mode
run_test_case ./pty/signal_char
run_test_case ./pty/termios2

if [ "${REGRESSION_TEST_PLATFORM:-asterinas}" = linux ]; then
    skip_test_case "The Linux regression VM uses a serial console." ./vt/vt_ioctl
else
    run_test_case ./vt/vt_ioctl
fi

run_test_case ./devtmpfs_mode
for test in evdev framebuffer; do
    if [ "${REGRESSION_TEST_PLATFORM:-asterinas}" = linux ]; then
        skip_test_case "The Linux regression VM has no input or display device." "./$test"
    else
        run_test_case "./$test"
    fi
done
run_test_case ./full
run_test_case_requiring CONFIG_HW_RANDOM_VIRTIO ./hwrng
run_test_case ./nvme
run_test_case ./random

finish_test_cases
