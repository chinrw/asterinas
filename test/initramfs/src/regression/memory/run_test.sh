#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

. "${REGRESSION_TEST_ROOT:-/test}/run_test_case.sh"

run_test_case ./mmap/mmap_and_fork
run_test_case ./mmap/mmap_and_mprotect
run_test_case ./mmap/mmap_and_mremap
run_test_case ./mmap/mmap_beyond_the_file
run_test_case ./mmap/mmap_err
run_test_case ./mmap/mmap_holes
run_test_case ./mmap/mmap_readahead
run_test_case ./mmap/mmap_shared_filebacked
run_test_case env TEST_TMPDIR=/ext2 ./mmap/mmap_vmrss
run_test_case ./mmap/rev_map_ext2
run_test_case ./mmap/rev_map_tmpfs

if [ "$(uname -m)" = x86_64 ]; then
    run_test_case ./mmap/mmap_32bit
fi

finish_test_cases
