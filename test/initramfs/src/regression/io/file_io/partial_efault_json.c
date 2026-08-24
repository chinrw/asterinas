// SPDX-License-Identifier: MPL-2.0

#define _GNU_SOURCE

#include <errno.h>
#include <stdio.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

static int fail_setup(const char *stage)
{
	printf("SYSSEC_RESULT_BEGIN\n");
	printf("{\"case_id\":\"pipe-partial-efault-read\","
	       "\"exit_kind\":\"setup-error\",\"stage\":\"%s\","
	       "\"errno\":%d}\n",
	       stage, errno);
	printf("SYSSEC_RESULT_END\n");
	return 2;
}

int main(void)
{
	long page_size = sysconf(_SC_PAGESIZE);
	char *mapping;
	char *fault_boundary;
	char remaining[2] = { 0, 0 };
	int pipe_fds[2];
	ssize_t first_ret;
	ssize_t remaining_ret;
	int first_errno;
	int remaining_errno;

	if (page_size <= 0)
		return fail_setup("sysconf");
	mapping = mmap(NULL, (size_t)page_size * 2, PROT_READ | PROT_WRITE,
		       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (mapping == MAP_FAILED)
		return fail_setup("mmap");
	if (mprotect(mapping + page_size, (size_t)page_size, PROT_NONE) < 0)
		return fail_setup("mprotect");
	if (pipe(pipe_fds) < 0)
		return fail_setup("pipe");
	if (write(pipe_fds[1], "AB", 2) != 2)
		return fail_setup("write");
	if (close(pipe_fds[1]) < 0)
		return fail_setup("close-write");

	fault_boundary = mapping + page_size - 1;
	fault_boundary[0] = 'X';
	errno = 0;
	first_ret = syscall(SYS_read, pipe_fds[0], fault_boundary, 2);
	first_errno = first_ret < 0 ? errno : 0;
	errno = 0;
	remaining_ret = syscall(SYS_read, pipe_fds[0], remaining,
				(size_t)sizeof(remaining));
	remaining_errno = remaining_ret < 0 ? errno : 0;

	printf("SYSSEC_RESULT_BEGIN\n");
	printf("{\"case_id\":\"pipe-partial-efault-read\","
	       "\"exit_kind\":\"normal\",\"return\":%zd,\"errno\":%d,"
	       "\"first_byte\":%u,\"remaining_return\":%zd,"
	       "\"remaining_errno\":%d,\"remaining_byte_0\":%u,"
	       "\"remaining_byte_1\":%u}\n",
	       first_ret, first_errno, (unsigned char)fault_boundary[0],
	       remaining_ret, remaining_errno, (unsigned char)remaining[0],
	       (unsigned char)remaining[1]);
	printf("SYSSEC_RESULT_END\n");

	close(pipe_fds[0]);
	munmap(mapping, (size_t)page_size * 2);
	return 0;
}
