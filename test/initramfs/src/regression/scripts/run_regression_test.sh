#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

SCRIPT_DIR=/test

# Run bio_stress first: the runner aborts on the first failing suite (set -e),
# and process/getcpu asserts cpu < 4, so it fails under SMP > 4 and would
# otherwise mask this probe.
for dir in "${SCRIPT_DIR}/bio_stress" $(find -L "${SCRIPT_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name bio_stress); do
    if [ -x "${dir}/run_test.sh" ]; then
        echo "Running test in $dir"
        (cd "$dir" && ./run_test.sh)
        echo "All test in $dir passed."
    else
        echo "Skipping $dir (no executable TEST_SCRIPT)"
    fi
done

echo "All regression tests passed."
