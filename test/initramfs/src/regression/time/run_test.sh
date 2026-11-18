#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

. "${REGRESSION_TEST_ROOT:-/test}/run_test_case.sh"

run_test_case ./clock_nanosleep/nanosleep_err

run_test_case ./gettimeofday/gettimeofday

run_test_case ./itimer/setitimer
run_test_case ./itimer/timer_create

run_test_case ./timerfd/timerfd_clockid
run_test_case ./timerfd/timerfd_readiness

finish_test_cases
