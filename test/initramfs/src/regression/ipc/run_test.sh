#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

. "${REGRESSION_TEST_ROOT:-/test}/run_test_case.sh"

run_test_case ./pipe/pipe_err
run_test_case ./pipe/process_pipe_available
run_test_case ./pipe/short_rw

run_test_case ./sem/sem

run_test_case ./shm/posix_shm

finish_test_cases
