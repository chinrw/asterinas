#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

# Stress the block-device page-cache read path to expose BIO completion-ordering
# races.
#
# The window this targets is the few instructions between submit and wait in
# `PageCacheBackend::read_page`: if a completion lands there, a buggy
# `SubmittedBio::complete()` publishes the terminal status before the callback
# marks the page up-to-date, and the faulting thread then maps an `Uninit` page.
#
# Faulting, not reading: the assertion that catches this lives in
# `VmoMapMode::ensure`, reached only through `commit_on()` from `vm_mapping.rs`.
# read(2) drives the same BIO but never checks the page state, so mmap plus a
# touch per page is what actually probes the defect.
#
# Hitting it needs three things at once, none of which the conformance suites
# provide together: SMP > 1, a debug build (so the `debug_assert` fires instead
# of being papered over by the page lock), and a storm of *cold* faults.
#
# One-sided: a panic proves the bug, a clean run proves nothing.

set -e

EXT2_DIR=/ext2
STRESS_DIR="${EXT2_DIR}/bio_stress"
READERS=8
FILES_PER_READER=32
FILE_SIZE_KB=256
ROUNDS=12

cleanup() {
    rm -rf "${STRESS_DIR}" 2>/dev/null || true
}
trap cleanup EXIT

echo "Populating ${STRESS_DIR}: ${READERS} x ${FILES_PER_READER} x ${FILE_SIZE_KB}K"
cleanup
mkdir -p "${STRESS_DIR}"
reader=1
while [ "${reader}" -le "${READERS}" ]; do
    mkdir -p "${STRESS_DIR}/d${reader}"
    file=1
    while [ "${file}" -le "${FILES_PER_READER}" ]; do
        dd if=/dev/urandom of="${STRESS_DIR}/d${reader}/f${file}" \
            bs=1K count="${FILE_SIZE_KB}" 2>/dev/null
        file=$((file + 1))
    done
    reader=$((reader + 1))
done
sync

round=1
while [ "${round}" -le "${ROUNDS}" ]; do
    # Writes leave every page hot, and Asterinas has no drop_caches. Remounting
    # destroys the fs instance along with its cached pages, so every fault below
    # is a guaranteed miss that submits a BIO.
    umount "${EXT2_DIR}"
    mount -t ext2 /dev/vda "${EXT2_DIR}"

    ./fault/mmap_cold_pages "${STRESS_DIR}" "${READERS}" "${FILES_PER_READER}"

    echo "Round ${round}/${ROUNDS} done."
    round=$((round + 1))
done

echo "All bio_stress tests passed."
