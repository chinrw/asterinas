/* SPDX-License-Identifier: MPL-2.0 */

#define _GNU_SOURCE
#include <fcntl.h>
#include <limits.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "../../common/test.h"

static char path[PATH_MAX];
static int fd;

static int same_time(struct timespec a, struct timespec b)
{
	return a.tv_sec == b.tv_sec && a.tv_nsec == b.tv_nsec;
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
}
END_SETUP()

FN_TEST(empty_writes_preserve_metadata_and_position)
{
	const off_t offsets[] = { 1, 2, 8192 };
	/* An old mtime exposes an update without relying on a sleep. */
	const struct timespec times[2] = { { 946684800, 0 }, { 946684800, 0 } };

	for (int positional = 0; positional < 2; positional++) {
		for (size_t i = 0; i < sizeof(offsets) / sizeof(offsets[0]);
		     i++) {
			const char *op = positional ? "pwrite" : "write";
			fprintf(stderr, "%s offset=%lld\n", op,
				(long long)offsets[i]);
			CHECK(ftruncate(fd, 0));
			CHECK_WITH(pwrite(fd, "hi", 2, 0), _ret == 2);
			CHECK(futimens(fd, times));
			struct stat before, after;
			CHECK(fstat(fd, &before));
			off_t position = positional ? 1 : offsets[i];
			CHECK_WITH(lseek(fd, position, SEEK_SET),
				   _ret == position);

			errno = 0;
			ssize_t written =
				positional ? pwrite(fd, "", 0, offsets[i]) :
					     write(fd, "", 0);
			int write_errno = errno;
			CHECK(fstat(fd, &after));
			off_t after_position = CHECK(lseek(fd, 0, SEEK_CUR));
			fprintf(stderr,
				"result=%zd expected=0; errno=%d expected=0; size=%lld expected=%lld; position=%lld expected=%lld\n",
				written, write_errno, (long long)after.st_size,
				(long long)before.st_size,
				(long long)after_position, (long long)position);
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
	CHECK(close(fd));
	CHECK(unlink(path));
}
END_SETUP()
