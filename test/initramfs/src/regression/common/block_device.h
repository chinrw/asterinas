/* SPDX-License-Identifier: MPL-2.0 */

#ifndef BLOCK_DEVICE_H
#define BLOCK_DEVICE_H

/*
 * Utilities to operate block devices, which work in both guest Asterinas and
 * host Linux.
 *
 * By opening a block device via open_block_device(), the device will be opened
 * in guest Asterinas via /dev/vda (or similar) and in host Linux via
 * /dev/loop0 (or similar). These correspond to the same device image, where
 * the underlying file system is prepared as part of the build process.
 * Linux regression VMs set `REGRESSION_BLOCK_DEVICES=virtio` to access their
 * dedicated /dev/vda, /dev/vdb, and /dev/vdc directly.
 *
 * The device can be mounted using mount_block_device(). After use, the mount
 * can be cleaned up and the device can be closed via umount_block_device() and
 * close_block_device(), respectively.
 */

#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdio.h>
#include <sys/mount.h>
#include <unistd.h>

enum block_device {
	BLOCK_VDA_EXT2,
	BLOCK_VDB_EXFAT,
	BLOCK_VDC_EXT2,
};

struct block_device_spec {
	const char *virtio_path;
	const char *image_path;
};

static const struct block_device_spec *block_device_spec(enum block_device dev)
{
	static const struct block_device_spec specs[] = {
		[BLOCK_VDA_EXT2] = { "/dev/vda",
				     "./test/initramfs/build/ext2.img" },
		[BLOCK_VDB_EXFAT] = { "/dev/vdb",
				      "./test/initramfs/build/exfat.img" },
		[BLOCK_VDC_EXT2] = { "/dev/vdc",
				     "./test/initramfs/build/ltp_dev.img" },
	};

	if ((size_t)dev >= sizeof(specs) / sizeof(specs[0])) {
		errno = ENODEV;
		return NULL;
	}
	return &specs[dev];
}

#ifndef __asterinas__

#include <linux/loop.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>

#include "test.h"

static bool should_use_virtio_block_devices(void)
{
	const char *mode = getenv("REGRESSION_BLOCK_DEVICES");

	return mode && strcmp(mode, "virtio") == 0;
}

static int open_loop_block_device(const char *image_path)
{
	int img_fd = CHECK(open(image_path, O_RDWR));
	int ctrl_fd = CHECK(open("/dev/loop-control", O_RDWR));
	int id = CHECK(ioctl(ctrl_fd, LOOP_CTL_GET_FREE));
	char loop_path[20];
	int loop_fd;

	CHECK(close(ctrl_fd));
	snprintf(loop_path, sizeof(loop_path), "/dev/loop%d", id);
	loop_fd = CHECK(open(loop_path, O_RDWR));
	CHECK(ioctl(loop_fd, LOOP_SET_FD, img_fd));
	CHECK(close(img_fd));

	return loop_fd;
}

#endif /* !__asterinas__ */

static int open_block_device(enum block_device dev)
{
	const struct block_device_spec *spec = block_device_spec(dev);

	if (!spec)
		return -1;
#ifndef __asterinas__
	if (!should_use_virtio_block_devices())
		return open_loop_block_device(spec->image_path);
#endif
	return open(spec->virtio_path, O_RDWR);
}

static int close_block_device(int fd)
{
#ifndef __asterinas__
	if (!should_use_virtio_block_devices())
		CHECK(ioctl(fd, LOOP_CLR_FD));
#endif
	return close(fd);
}

static int __attribute__((unused)) mount_block_device(int fd, const char *path,
						      const char *fs, int flags)
{
	char buf[25];
	snprintf(buf, sizeof(buf), "/proc/self/fd/%d", fd);

	return mount(buf, path, fs, flags, NULL);
}

static int __attribute__((unused)) umount_block_device(const char *path)
{
	return umount(path);
}

#endif /* BLOCK_DEVICE_H */
