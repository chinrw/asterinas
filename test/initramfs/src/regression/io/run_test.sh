#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

. "${REGRESSION_TEST_ROOT:-/test}/run_test_case.sh"

run_test_case ./epoll/epoll_cycle
run_test_case ./epoll/epoll_err
run_test_case ./epoll/poll_err
run_test_case ./epoll/test_epoll_pwait.sh

run_test_case ./eventfd2/eventfd2
run_test_case ./eventfd2/eventfd2_err

run_test_case ./file_io/access_err
run_test_case ./file_io/block_device
run_test_case ./file_io/fcntl_lock
run_test_case ./file_io/fcntl_owner
run_test_case ./file_io/fcntl_status_flags
run_test_case ./file_io/file_err
run_test_case ./file_io/iovec_err

run_test_case ./epoll/epoll_wait

finish_test_cases
