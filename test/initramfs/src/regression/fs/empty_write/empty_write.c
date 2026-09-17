/* SPDX-License-Identifier: MPL-2.0 */

#define _GNU_SOURCE
#include <fcntl.h>
#include <limits.h>
#include <sys/stat.h>
#include <sys/uio.h>
#include <time.h>
#include <unistd.h>

#include "../../common/test.h"

/*
 * Far enough past EOF to cross a block boundary, so an empty write that is
 * allowed to publish its offset changes both the file size and the allocated
 * block count.
 */
static const off_t far_past_eof_offset = 8192;
/* 2000-01-01T00:00:00Z: an old timestamp exposes an update without a sleep. */
static const time_t old_timestamp_sec = 946684800;

static const off_t offsets[] = { 1, 2, far_past_eof_offset };

/*
 * Seeded non-zero so that a positional write which wrongly moves the
 * descriptor offset is visible in the position check below.
 */
static const off_t positional_seed_offset = 1;

static char path[PATH_MAX];
static int fd;
static int io_fd;
/* A valid user-space address for the empty iovecs used by the vector cases. */
static char empty_buffer[1];

static int same_time(struct timespec a, struct timespec b)
{
	return a.tv_sec == b.tv_sec && a.tv_nsec == b.tv_nsec;
}

enum empty_write_op {
	OP_WRITE,
	OP_PWRITE,
	OP_PWRITEV,
};

static const char *op_name(enum empty_write_op op)
{
	switch (op) {
	case OP_WRITE:
		return "write";
	case OP_PWRITE:
		return "pwrite";
	case OP_PWRITEV:
		return "pwritev";
	}
	return "unknown";
}

/* The positional operations must leave the descriptor offset untouched. */
static int op_is_positional(enum empty_write_op op)
{
	return op != OP_WRITE;
}

/* Issues the zero-byte write for `op` and returns its return value. */
static ssize_t do_empty_write(enum empty_write_op op, off_t offset)
{
	struct iovec iov[2] = {
		{ .iov_base = empty_buffer, .iov_len = 0 },
		{ .iov_base = empty_buffer, .iov_len = 0 },
	};

	switch (op) {
	case OP_WRITE:
		return write(io_fd, "", 0);
	case OP_PWRITE:
		return pwrite(io_fd, "", 0, offset);
	case OP_PWRITEV:
		return pwritev(io_fd, iov, 2, offset);
	}

	errno = EINVAL;
	return -1;
}

FN_SETUP(create_file)
{
	const char *dir = getenv("TEST_TMPDIR");
	if (!dir) {
		dir = "/tmp";
	}
	CHECK_WITH(snprintf(path, sizeof(path), "%s/empty-write-XXXXXX", dir),
		   _ret > 0 && (size_t)_ret < sizeof(path));
	fd = CHECK(mkstemp(path));
	/*
	 * A second descriptor on the same file: the descriptor under test may
	 * carry `O_DIRECT`, which would reject the unaligned 2-byte setup write
	 * below, so `fd` stays buffered for the setup and the metadata calls.
	 */
	int direct = getenv("TEST_DIRECT") != NULL;
	io_fd = CHECK(open(path, O_RDWR | (direct ? O_DIRECT : 0)));
	fprintf(stderr, "fixture=%s mode=%s\n", dir,
		direct ? "direct" : "buffered");
}
END_SETUP()

FN_TEST(empty_writes_preserve_metadata_and_position)
{
	/* An old mtime exposes an update without relying on a sleep. */
	const struct timespec times[2] = { { old_timestamp_sec, 0 },
					   { old_timestamp_sec, 0 } };
	const enum empty_write_op ops[] = { OP_WRITE, OP_PWRITE, OP_PWRITEV };

	for (size_t op = 0; op < sizeof(ops) / sizeof(ops[0]); op++) {
		for (size_t i = 0; i < sizeof(offsets) / sizeof(offsets[0]);
		     i++) {
			const char *name = op_name(ops[op]);
			off_t position = op_is_positional(ops[op]) ?
						 positional_seed_offset :
						 offsets[i];
			fprintf(stderr, "%s offset=%lld\n", name,
				(long long)offsets[i]);
			CHECK(ftruncate(fd, 0));
			CHECK_WITH(pwrite(fd, "hi", 2, 0), _ret == 2);
			CHECK(futimens(fd, times));
			struct stat before, after;
			CHECK(fstat(fd, &before));
			CHECK_WITH(lseek(io_fd, position, SEEK_SET),
				   _ret == position);

			errno = 0;
			ssize_t written = do_empty_write(ops[op], offsets[i]);
			int write_errno = errno;
			CHECK(fstat(fd, &after));
			off_t after_position = CHECK(lseek(io_fd, 0, SEEK_CUR));
			fprintf(stderr,
				"result=%zd expected=0; errno=%d expected=0; size=%lld expected=%lld; position=%lld expected=%lld\n",
				written, write_errno, (long long)after.st_size,
				(long long)before.st_size,
				(long long)after_position, (long long)position);
			fprintf(stderr, "blocks=%lld expected=%lld\n",
				(long long)after.st_blocks,
				(long long)before.st_blocks);
			fprintf(stderr,
				"mtime=%lld.%09ld expected=%lld.%09ld; ctime=%lld.%09ld expected=%lld.%09ld\n",
				(long long)after.st_mtim.tv_sec,
				after.st_mtim.tv_nsec,
				(long long)before.st_mtim.tv_sec,
				before.st_mtim.tv_nsec,
				(long long)after.st_ctim.tv_sec,
				after.st_ctim.tv_nsec,
				(long long)before.st_ctim.tv_sec,
				before.st_ctim.tv_nsec);
			TEST_RES(written, _ret == 0);
			TEST_RES(write_errno, _ret == 0);
			TEST_RES(after.st_size, _ret == before.st_size);
			TEST_RES(after.st_blocks, _ret == before.st_blocks);
			TEST_RES(same_time(before.st_mtim, after.st_mtim),
				 _ret);
			TEST_RES(same_time(before.st_ctim, after.st_ctim),
				 _ret);
			TEST_RES(after_position, _ret == position);
		}
	}
}
END_TEST()

FN_SETUP(cleanup)
{
	CHECK(close(io_fd));
	CHECK(close(fd));
	CHECK(unlink(path));
}
END_SETUP()
