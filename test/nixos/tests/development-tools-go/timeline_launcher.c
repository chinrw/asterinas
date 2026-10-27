// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
#define CONTROL 0x41535454
static volatile sig_atomic_t child_pid, timeout_phase, stopped, saved_bytes;
static void deadline(int signal_number) {
  (void)signal_number;
  saved_bytes = (sig_atomic_t)syscall(SYS_prctl, CONTROL, 2UL, 0UL, 0UL, 0UL);
  stopped = 1;
  timeout_phase = 1;
  kill(child_pid, SIGKILL);
}
int main(int argc, char **argv) {
  if (argc != 5)
    return 2;
  int mode = atoi(argv[1]);
  if (prctl(CONTROL, 0UL, 0UL, 0UL, 0UL) != 1) {
    perror("timeline probe version");
    return 3;
  }
  int gate[2];
  if (pipe(gate)) {
    perror("pipe");
    return 4;
  }
  char log_name[512];
  if (snprintf(log_name, sizeof(log_name), "%s.child.log", argv[2]) >=
      (int)sizeof(log_name))
    return 5;
  int log = open(log_name, O_CREAT | O_TRUNC | O_WRONLY, 0600);
  if (log < 0) {
    perror("log");
    return 6;
  }
  pid_t pid = fork();
  if (pid < 0) {
    perror("fork");
    return 7;
  }
  if (pid == 0) {
    close(gate[1]);
    dup2(log, STDOUT_FILENO);
    dup2(log, STDERR_FILENO);
    close(log);
    char byte;
    if (read(gate[0], &byte, 1) != 1)
      _exit(8);
    close(gate[0]);
    setenv("GOMAXPROCS", "1", 1);
    execl(argv[3], argv[3], argv[4], (char *)NULL);
    perror("exec");
    _exit(9);
  }
  close(log);
  close(gate[0]);
  child_pid = pid;
  if (prctl(CONTROL, 1UL, (unsigned long)pid, (unsigned long)mode, 0UL)) {
    perror("timeline start");
    kill(pid, SIGKILL);
    waitpid(pid, NULL, 0);
    return 10;
  }
  printf("TIMELINE_CHILD pid=%d mode=%d\n", pid, mode);
  fflush(stdout);
  struct sigaction action = {.sa_handler = deadline};
  sigemptyset(&action.sa_mask);
  sigaction(SIGALRM, &action, NULL);
  alarm(10);
  if (write(gate[1], "x", 1) != 1) {
    perror("gate");
    kill(pid, SIGKILL);
  }
  close(gate[1]);
  int status;
  while (waitpid(pid, &status, 0) < 0) {
    if (errno != EINTR) {
      perror("waitpid");
      return 11;
    }
  }
  alarm(0);
  long bytes = stopped ? saved_bytes : prctl(CONTROL, 2UL, 0UL, 0UL, 0UL);
  if (bytes < 0) {
    perror("timeline stop");
    return 12;
  }
  void *buffer = mmap(NULL, (size_t)bytes, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (buffer == MAP_FAILED) {
    perror("buffer");
    return 13;
  }
  if (prctl(CONTROL, 3UL, (unsigned long)buffer, (unsigned long)bytes, 0UL) !=
      bytes) {
    perror("timeline read");
    return 14;
  }
  FILE *file = fopen(argv[2], "wb");
  if (!file || fwrite(buffer, 1, (size_t)bytes, file) != (size_t)bytes ||
      fclose(file)) {
    perror("trace write");
    return 15;
  }
  munmap(buffer, (size_t)bytes);
  log = open(log_name, O_RDONLY);
  if (log < 0)
    return 16;
  char text[4096];
  ssize_t count;
  while ((count = read(log, text, sizeof(text))) > 0) {
    ssize_t done = 0;
    while (done < count) {
      ssize_t n = write(STDOUT_FILENO, text + done, (size_t)(count - done));
      if (n < 0)
        return 17;
      done += n;
    }
  }
  close(log);
  printf(
      "TIMELINE_REAP pid=%d raw_status=%d timeout_phase=%d trace_bytes=%ld\n",
      pid, status, timeout_phase, bytes);
  fflush(stdout);
  if (timeout_phase)
    return 124;
  return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
