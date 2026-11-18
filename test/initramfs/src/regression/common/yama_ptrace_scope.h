/* SPDX-License-Identifier: MPL-2.0 */

#ifndef YAMA_PTRACE_SCOPE_H
#define YAMA_PTRACE_SCOPE_H

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#include "test.h"

#define YAMA_SCOPE_UNAVAILABLE (-1)
#define YAMA_SCOPE_NO_ATTACH 3

static inline __attribute__((unused)) int read_yama_scope(void)
{
	const char *path = "/proc/sys/kernel/yama/ptrace_scope";
#ifdef __asterinas__
	int fd = CHECK(open(path, O_RDONLY));
#else
	// Linux can provide ptrace without the optional Yama LSM.
	int fd = CHECK_WITH(open(path, O_RDONLY), _ret >= 0 || errno == ENOENT);
	if (fd < 0)
		return YAMA_SCOPE_UNAVAILABLE;
#endif
	char buf[32] = { 0 };
	ssize_t nread = CHECK(read(fd, buf, sizeof(buf) - 1));
	CHECK(close(fd));
	buf[nread] = '\0';
	return atoi(buf);
}

static inline __attribute__((unused)) void write_yama_scope(int scope)
{
	int fd = CHECK(open("/proc/sys/kernel/yama/ptrace_scope", O_RDWR));
	char buf[32] = { 0 };
	int len = CHECK(snprintf(buf, sizeof(buf), "%d\n", scope));
	CHECK(write(fd, buf, len));
	CHECK(close(fd));
}

#endif
