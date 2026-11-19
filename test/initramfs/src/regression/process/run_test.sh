#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

. "${REGRESSION_TEST_ROOT:-/test}/run_test_case.sh"

if [ "$(uname -m)" = "x86_64" ]; then
    run_test_case ./arch_prctl/fsgsbase
fi

run_test_case ./clone3/clone_exit_signal
run_test_case ./clone3/clone_files
run_test_case ./clone3/clone_invalid_exit_signal
run_test_case ./clone3/clone_no_exit_signal
run_test_case ./clone3/clone_parent
run_test_case ./clone3/clone_process

run_test_case ./cpu_affinity/cpu_affinity
run_test_case ./cpu_affinity/permission

run_test_case ./execve/execve
run_test_case ./execve/execve_comm
run_test_case ./execve/execve_err
run_test_case ./execve/execve_memfd
run_test_case ./execve/execve_mt_parent
run_test_case ./execve/execveat

run_test_case ./exit/exit_code
run_test_case ./exit/exit_procfs

[ "$(uname -m)" = "x86_64" ] && run_test_case ./fork/fork
run_test_case ./fork_c/fork

run_test_case ./getcpu/getcpu

run_test_case ./getpid/getpid

run_test_case ./personality/personality

run_test_case ./prctl/capbset
run_test_case ./prctl/no_new_privs
run_test_case ./prctl/secure_bits
run_test_case ./prctl/subreaper
run_test_case ./prctl/thread_name

run_test_case ./pthread/pthread_signal_test
run_test_case ./pthread/pthread_test

run_test_case ./ptrace/ptrace
run_test_case ./ptrace/set_options

if [ "$(uname -m)" = "x86_64" ]; then
    run_test_case ./ptrace/debugger
    run_test_case ./ptrace/read_write_regs
fi

run_test_case ./sched/sched_attr_getset
run_test_case ./sched/sched_param_getset
run_test_case ./sched/sched_param_idle
run_test_case ./sched/sched_set_permission
run_test_case ./sched/setpriority_permission

run_test_case ./signal/kill
run_test_case ./signal/parent_death_signal
run_test_case ./signal/pidfd_send_signal
run_test_case ./signal/rt_sigpending
run_test_case ./signal/signal_fd
run_test_case ./signal/signal_test2

if [ "$(uname -m)" = "x86_64" ]; then
    run_test_case ./signal/fault_signals
    run_test_case ./signal/sigaltstack
    run_test_case ./signal/signal_fpu
    run_test_case ./signal/signal_rflags_df
    run_test_case ./signal/signal_test
    run_test_case ./signal/sigtrap
fi

run_test_case ./cgroup.sh
run_test_case ./group_session
run_test_case ./job_control
run_test_case ./pidfd
run_test_case ./pidfd_getfd
run_test_case ./set_tid_address
run_test_case ./wait4

finish_test_cases
