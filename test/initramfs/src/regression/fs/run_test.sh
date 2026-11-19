#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

. "${REGRESSION_TEST_ROOT:-/test}/run_test_case.sh"

check_file_size() {
    local file_name="$1"
    local expected_size="$2"

    if [ ! -f "$file_name" ]; then
        echo "Error: File does not exist."
        return 1
    fi

    actual_size=$(du -b "$file_name" | cut -f1)

    if [ "$actual_size" -eq "$expected_size" ]; then
        return 0
    else
        echo "Error: File size is incorrect: expected ${expected_size}, but got ${actual_size}."
        return 1
    fi
}

test_ext2() {
    local ext2_dir="$1"
    local test_file="$2"

    cd ${ext2_dir}

    # Test case for the big file feature
    for i in $(seq 1 10); do
        truncate -s 500M ${test_file}
        check_file_size ${test_file} $((500 * 1024 * 1024))
        truncate -s 2K ${test_file}
        check_file_size ${test_file} $((2 * 1024))
    done

    # Clean up
    rm -f ${test_file}
    sync
    cd -
}

test_fdatasync() {
    ./fdatasync/fdatasync "$1"
    rm -f "${1%/}/test_fdatasync.txt"
}

test_mount_bind_file() {
    local file_a="/file_a.txt"
    local file_b="/file_b.txt"
    local content_a="initial content for file A"
    local content_b_new="new content written to file B"

    echo "$content_a" > "$file_a"
    touch "$file_b"

    mount --bind "$file_a" "$file_b"

    # Read from file_b and check if it matches file_a's content
    if [ "$(cat "$file_b")" != "$content_a" ]; then
        echo "Error: Read from bind-mounted file failed. Content mismatch."
        umount "$file_b"
        rm -f "$file_a" "$file_b"
        return 1
    fi

    echo "$content_b_new" > "$file_b"

    # Check if file_a's content is updated
    if [ "$(cat "$file_a")" != "$content_b_new" ]; then
        echo "Error: Write to bind-mounted file did not affect the source file."
        umount "$file_b"
        rm -f "$file_a" "$file_b"
        return 1
    fi

    umount "$file_b"
    rm -f "$file_a" "$file_b"
}

run_test_case test_ext2 "/ext2" "test_file.txt"
run_test_case ./ext2/fallocate
run_test_case ./ext2/file_io
run_test_case ./ext2/mknod
run_test_case ./ext2/namei
run_test_case ./ext2/open_dir
run_test_case ./ext2/open_unlink
run_test_case ./ext2/permissions
run_test_case ./ext2/readdir
run_test_case ./ext2/rename
run_test_case ./ext2/rmdir
run_test_case ./ext2/shared_block_device
run_test_case ./ext2/short_rw
run_test_case ./ext2/sparse
run_test_case ./ext2/sticky
run_test_case ./ext2/symlink
run_test_case ./ext2/unix_socket
run_test_case_requiring CONFIG_EXT2_FS_XATTR ./ext2/xattr
run_test_case test_fdatasync /
run_test_case test_fdatasync /ext2
run_test_case_with_linux_module exfat test_fdatasync /exfat

run_test_case test_mount_bind_file

run_test_case env TEST_TMPDIR=/tmp TEST_DIRECT=0 ./empty_write/empty_write
run_test_case_with_linux_module exfat env TEST_TMPDIR=/exfat TEST_DIRECT=0 ./empty_write/empty_write
run_test_case_with_linux_module exfat env TEST_TMPDIR=/exfat TEST_DIRECT=1 ./empty_write/empty_write
run_test_case env TEST_TMPDIR=/ext2 TEST_DIRECT=0 ./empty_write/empty_write
run_test_case env TEST_TMPDIR=/ext2 TEST_DIRECT=1 ./empty_write/empty_write

run_test_case ./getcwd/getcwd

run_test_case ./inotify/inotify_align
run_test_case ./inotify/inotify_close
run_test_case ./inotify/inotify_o_path
run_test_case ./inotify/inotify_poll
run_test_case ./inotify/inotify_unlink

run_test_case ./isolation/chroot
run_test_case ./isolation/pivot_root

run_test_case ./mount/listmount
run_test_case ./mount/mount_api
run_test_case ./mount/mount_capability
run_test_case ./mount/mount_flag_enforcement
run_test_case ./mount/mount_move
run_test_case ./mount/mount_propagation
run_test_case ./mount/mount_readonly

run_test_case env TEST_TMPDIR=/tmp ./name_max/name_max
run_test_case env TEST_TMPDIR=/ext2 ./name_max/name_max

run_test_case_with_linux_module overlay ./overlayfs/lchown_lower_symlink
run_test_case_with_linux_module overlay ./overlayfs/link_lower_file
run_test_case_with_linux_module overlay ./overlayfs/mknod_whiteout_rejected
run_test_case_with_linux_module overlay ./overlayfs/ovl_test
run_test_case_with_linux_module overlay ./overlayfs/readdir_small_buffer
run_test_case_with_linux_module overlay ./overlayfs/rmdir_lower_only_nonempty
run_test_case_with_linux_module overlay ./overlayfs/sparse_copyup_consistency
run_test_case_with_linux_module overlay ./overlayfs/xino_dino_identity

run_test_case ./procfs/dentry_cache
run_test_case ./procfs/fd
run_test_case ./procfs/getdents
run_test_case ./procfs/mountstats
run_test_case ./procfs/pid_mem
run_test_case ./procfs/proc_fd_open_fifo_after_setid
run_test_case ./procfs/proc_sys_kernel
run_test_case ./procfs/tid

run_test_case ./pseudofs/fallocate
run_test_case ./pseudofs/memfd_access_err
run_test_case ./pseudofs/memfd_create
run_test_case ./pseudofs/pseudo_dentry
run_test_case ./pseudofs/pseudo_dev_id
run_test_case ./pseudofs/pseudo_inode
run_test_case ./pseudofs/pseudo_mount

run_test_case ./ramfs/readdir

run_test_case ./rename/permission
run_test_case ./rename/same_inode

run_test_case_with_linux_module exfat ./statx/btime

run_test_case ./symlink/symlink

run_test_case ./sync/sync

if [ "$(uname -m)" = x86_64 ]; then
    run_test_case ./sysfs/device/mem
    run_test_case ./sysfs/device/pci
fi

run_test_case ./tmpfile/tmpfile

run_test_case ./utimensat/utimensat

finish_test_cases
