#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

. "${REGRESSION_TEST_ROOT:-/test}/run_test_case.sh"

run_test_case ./capability/ambient
run_test_case ./capability/capabilities
run_test_case ./capability/capset
run_test_case ./capability/execve
run_test_case ./capability/kill
run_test_case ./capability/reboot
run_test_case ./capability/setgroups
run_test_case ./capability/trusted_xattr

run_test_case ./lsm/module_selection
run_test_case_requiring CONFIG_SECURITY_YAMA ./lsm/yama

run_test_case ./namespace/cgroup_ns
run_test_case ./namespace/ipc_ns_sem
run_test_case ./namespace/mnt_ns
run_test_case ./namespace/proc_nsfs
run_test_case ./namespace/setns
run_test_case ./namespace/unshare

finish_test_cases
