#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

_regression_case_number=0
_regression_case_matches=0
REGRESSION_TEST_GROUP=${REGRESSION_TEST_GROUP:-$(basename "$PWD")}

case "${REGRESSION_TEST_MODE:-run}" in
    run|list) ;;
    *) echo "Invalid regression test mode: $REGRESSION_TEST_MODE" >&2; exit 2 ;;
esac
case "${REGRESSION_TEST_CASE:-}" in
    '') ;;
    0*|*[!0-9]*) echo "Invalid regression case: $REGRESSION_TEST_CASE" >&2; exit 2 ;;
esac

run_test_case() {
    _register_test_case RUN "" "" "$@"
}

run_test_case_with_linux_module() {
    # The module is prepared only for the selected workload in the benchmark guest.
    _register_test_case RUN "" "$@"
}

# Check requirements that must be built into the benchmark kernel.
run_test_case_requiring() {
    _regression_config=$1
    shift
    if [ -n "${REGRESSION_BUILTIN_CONFIG:-}" ] &&
        ! grep -Fxq "$_regression_config=y" "$REGRESSION_BUILTIN_CONFIG"; then
        skip_test_case "[kernel config] The benchmark kernel lacks built-in $_regression_config." "$@"
    else
        run_test_case "$@"
    fi
}

skip_test_case() {
    _regression_skip_reason=$1
    shift
    _register_test_case SKIP "$_regression_skip_reason" "" "$@"
}

_register_test_case() {
    _regression_case_action=$1
    _regression_case_reason=$2
    _regression_module=$3
    shift 3
    if [ "$#" -eq 0 ]; then
        echo "A regression case must name a command." >&2
        exit 2
    fi
    if [ "$_regression_case_action" = SKIP ] && [ -z "$_regression_case_reason" ]; then
        echo "A skipped regression case must have a reason." >&2
        exit 2
    fi
    _regression_case_number=$((_regression_case_number + 1))

    if [ "${REGRESSION_TEST_MODE:-run}" = list ]; then
        {
            printf '%s\t%s\t%s\t' "$REGRESSION_TEST_GROUP" "$_regression_case_number" "$_regression_case_action"
            printf '%s ' "$@"
            printf '\t%s\n' "$_regression_case_reason"
        } >> "${REGRESSION_TEST_MANIFEST:?Missing regression manifest path}"
        return
    fi

    if [ -n "${REGRESSION_TEST_CASE:-}" ] && \
        [ "$_regression_case_number" != "$REGRESSION_TEST_CASE" ]; then
        return
    fi
    _regression_case_matches=$((_regression_case_matches + 1))
    if [ "$_regression_case_action" = SKIP ]; then
        printf 'Skipping %s/%s: %s\n' "$REGRESSION_TEST_GROUP" "$_regression_case_number" "$_regression_case_reason"
        return
    fi

    if [ -n "${REGRESSION_LINUX_MODULE_DIR:-}" ] && [ -n "$_regression_module" ]; then
        /bin/busybox insmod "$REGRESSION_LINUX_MODULE_DIR/$_regression_module.ko"
        if [ "$_regression_module" = exfat ]; then
            mkdir -p /exfat
            mount -t exfat /dev/vdb /exfat
        fi
    fi

    printf 'REGRESSION_CASE_START %s %s\n' "$REGRESSION_TEST_GROUP" "$_regression_case_number"
    printf 'Running regression case %s/%s:' "$REGRESSION_TEST_GROUP" "$_regression_case_number"
    printf ' %s' "$@"
    printf '\n'
    # Keep errexit active for shell functions and isolate each case's cwd and traps.
    ( "$@" )
}

finish_test_cases() {
    if [ "$_regression_case_number" -eq 0 ]; then
        echo "No regression cases were registered in $REGRESSION_TEST_GROUP." >&2
        exit 1
    fi
    if [ "${REGRESSION_TEST_MODE:-run}" != list ] && \
        [ -n "${REGRESSION_TEST_CASE:-}" ] && [ "$_regression_case_matches" -ne 1 ]; then
        echo "Unknown regression case: $REGRESSION_TEST_GROUP/$REGRESSION_TEST_CASE" >&2
        exit 2
    fi
}
