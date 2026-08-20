// SPDX-License-Identifier: MPL-2.0

// Fault in cold, file-backed pages from many processes at once.
//
// The BIO completion-ordering race is only observable on the *mapping* path:
// `commit_on()` -> `VmoMapMode::ensure()` asserts the page is initialized, and
// only `vm_mapping.rs` (page faults) reaches it. Plain read(2) exercises the
// same page-cache read but never checks that state, so a read-based stress
// test cannot expose the defect no matter how much I/O it does.
//
// Each child mmaps its own files and touches one byte per page, so every fault
// is a first touch on a page no other process is racing for -- sharing a page
// would park the second faulter on the page lock, which is the safe path.

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#define PAGE_SIZE 4096

static int fault_file(const char *path)
{
	int fd = open(path, O_RDONLY);
	if (fd < 0) {
		perror("open");
		return -1;
	}

	struct stat st;
	if (fstat(fd, &st) < 0) {
		perror("fstat");
		close(fd);
		return -1;
	}

	char *map = mmap(NULL, st.st_size, PROT_READ, MAP_SHARED, fd, 0);
	if (map == MAP_FAILED) {
		perror("mmap");
		close(fd);
		return -1;
	}

	// Touching one byte per page is what drives commit_on() -> ensure().
	// `volatile` keeps the compiler from eliding the loads.
	volatile char sink = 0;
	for (off_t off = 0; off < st.st_size; off += PAGE_SIZE)
		sink ^= map[off];
	(void)sink;

	munmap(map, st.st_size);
	close(fd);
	return 0;
}

int main(int argc, char **argv)
{
	if (argc != 4) {
		fprintf(stderr, "usage: %s <dir> <children> <files-per-child>\n",
			argv[0]);
		return 1;
	}

	const char *dir = argv[1];
	int children = atoi(argv[2]);
	int files = atoi(argv[3]);

	for (int c = 1; c <= children; c++) {
		pid_t pid = fork();
		if (pid < 0) {
			perror("fork");
			return 1;
		}
		if (pid == 0) {
			char path[512];
			for (int f = 1; f <= files; f++) {
				snprintf(path, sizeof(path), "%s/d%d/f%d", dir,
					 c, f);
				if (fault_file(path) < 0)
					_exit(1);
			}
			_exit(0);
		}
	}

	int failures = 0;
	for (int c = 0; c < children; c++) {
		int status = 0;
		if (wait(&status) < 0) {
			perror("wait");
			return 1;
		}
		if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
			failures++;
	}

	if (failures) {
		fprintf(stderr, "%d child(ren) failed\n", failures);
		return 1;
	}
	return 0;
}
