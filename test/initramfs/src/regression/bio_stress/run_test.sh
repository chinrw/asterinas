#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

# Stress the block-device page-cache read path to expose BIO completion-ordering
# races.
#
# The window this targets is the few instructions between submit and wait in
# `PageCacheBackend::read_page`: if a completion lands there, a buggy
# `SubmittedBio::complete()` publishes the terminal status before the callback
# marks the page up-to-date, and the mapping code then sees an `Uninit` page.
#
# Hitting it needs three things at once, none of which the conformance suites
# provide together: SMP > 1, a debug build (so the `debug_assert` fires instead
# of being papered over by the page lock), and a storm of *cold* page-cache
# reads. Readers must not share files -- a second thread faulting the same page
# blocks on the page lock, which is the safe path; only first touches are
# exposed.
#
# One-sided: a panic proves the bug, a clean run proves nothing.

set -e

EXT2_DIR=/ext2
STRESS_DIR="${EXT2_DIR}/bio_stress"
READERS=8
FILES_PER_READER=48
FILE_SIZE_KB=64
ROUNDS=6

cleanup() {
    rm -rf "${STRESS_DIR}" 2>/dev/null || true
}
trap cleanup EXIT

echo "Populating ${STRESS_DIR}: ${READERS} readers x ${FILES_PER_READER} files x ${FILE_SIZE_KB}K"
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
    # destroys the fs instance along with its cached pages, so the reads below
    # are guaranteed misses that each submit a BIO.
    umount "${EXT2_DIR}"
    mount -t ext2 /dev/vda "${EXT2_DIR}"

    reader=1
    while [ "${reader}" -le "${READERS}" ]; do
        cat "${STRESS_DIR}/d${reader}"/* > /dev/null &
        reader=$((reader + 1))
    done
    wait

    echo "Round ${round}/${ROUNDS} done."
    round=$((round + 1))
done

# Verify the data actually survived the race window: a page published before its
# callback ran would read back as zeros rather than the written content.
if [ ! -s "${STRESS_DIR}/d1/f1" ]; then
    echo "Error: stress file is empty after cold reads."
    exit 1
fi

echo "All bio_stress tests passed."
