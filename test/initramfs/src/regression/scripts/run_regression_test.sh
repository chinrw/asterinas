#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

SCRIPT_DIR=${REGRESSION_TEST_ROOT:-/test}

if [ "$#" -gt 1 ]; then
    echo "Usage: $0 [group]" >&2
    exit 2
fi
if [ -n "${REGRESSION_TEST_CASE:-}" ] && [ "$#" -ne 1 ]; then
    echo "Selecting a regression case requires exactly one group." >&2
    exit 2
fi

if [ "$#" -eq 1 ]; then
    case "$1" in
        ''|*[!a-zA-Z0-9_]*)
            echo "Invalid regression group: $1" >&2
            exit 2
            ;;
    esac
    directories="${SCRIPT_DIR}/$1"
else
    directories=$(find -L "${SCRIPT_DIR}" -mindepth 1 -maxdepth 1 -type d | sort)
fi

completed=0
for dir in $directories; do
    if [ ! -x "${dir}/run_test.sh" ]; then
        echo "Missing executable regression entry: ${dir}/run_test.sh" >&2
        exit 1
    fi
    echo "Running test in $dir"
    export REGRESSION_TEST_GROUP=${dir##*/}
    (cd "$dir" && ./run_test.sh)
    completed=$((completed + 1))
    if [ "${REGRESSION_TEST_MODE:-run}" != list ]; then
        echo "Regression entry in $dir completed."
    fi
done

if [ "$completed" -eq 0 ]; then
    echo "No regression groups were executed." >&2
    exit 1
fi

if [ "${REGRESSION_TEST_MODE:-run}" = list ]; then
    echo "Regression cases were listed."
elif [ -n "${REGRESSION_TEST_CASE:-}" ]; then
    echo "Selected regression workload passed."
else
    echo "All regression tests passed."
fi
