#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

. "${REGRESSION_TEST_ROOT:-/test}/run_test_case.sh"

run_test_case ./in_c/hello
run_test_case ./in_c_pie/hello
case "$(uname -m)" in
    x86_64|riscv64|aarch64)
        run_test_case ./in_assembly/hello
        ;;
esac

finish_test_cases
