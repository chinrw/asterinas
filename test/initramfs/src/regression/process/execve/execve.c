// SPDX-License-Identifier: MPL-2.0

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main()
{
	char *argv[] = { "argv1", "argv2", NULL };
	char *envp[] = { "home=/", "version=1.1", NULL };
	printf("Execve a new file /test/process/execve/hello:\n");
	// Flush stdout before execve replaces this process.
	fflush(stdout);
	execve("/test/process/execve/hello", argv, envp);
	perror("execve");
	return EXIT_FAILURE;
}
