// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

static uint64_t now_ns(void) {
  struct timespec value;
  if (clock_gettime(CLOCK_MONOTONIC, &value))
    _exit(2);
  return (uint64_t)value.tv_sec * 1000000000 + value.tv_nsec;
}
static struct timespec as_time(uint64_t ns) {
  return (struct timespec){.tv_sec = ns / 1000000000,
                           .tv_nsec = ns % 1000000000};
}
static volatile sig_atomic_t received;
static void handler(int signal_number) {
  (void)signal_number;
  received = 1;
}
static void *interrupt_main(void *argument) {
  struct timespec delay = as_time(10000000);
  if (syscall(SYS_nanosleep, &delay, NULL))
    _exit(3);
  if (pthread_kill(*(pthread_t *)argument, SIGUSR1))
    _exit(4);
  return NULL;
}
int main(void) {
  const uint64_t durations[] = {1000, 20000, 1000000, 10000000};
  for (int kind = 0; kind < 3; kind++) {
    for (unsigned int i = 0; i < sizeof(durations) / sizeof(durations[0]);
         i++) {
      uint64_t start = now_ns(), deadline = start + durations[i];
      struct timespec request = as_time(kind == 2 ? deadline : durations[i]);
      long result =
          kind == 0 ? syscall(SYS_nanosleep, &request, NULL)
                    : syscall(SYS_clock_nanosleep, CLOCK_MONOTONIC,
                              kind == 2 ? TIMER_ABSTIME : 0, &request, NULL);
      if (result || now_ns() < deadline) {
        fprintf(
            stderr,
            "sleep returned early or failed: kind=%d request_ns=%lu errno=%d\n",
            kind, (unsigned long)durations[i], errno);
        return 5;
      }
    }
  }
  struct timespec past = as_time(1);
  if (syscall(SYS_clock_nanosleep, CLOCK_MONOTONIC, TIMER_ABSTIME, &past, NULL))
    return 6;
  struct sigaction action = {.sa_handler = handler};
  sigemptyset(&action.sa_mask);
  if (sigaction(SIGUSR1, &action, NULL))
    return 7;
  pthread_t main_thread = pthread_self(), sender;
  if (pthread_create(&sender, NULL, interrupt_main, &main_thread))
    return 8;
  struct timespec request = as_time(1000000000), remaining = {0};
  long result = syscall(SYS_nanosleep, &request, &remaining);
  int error = errno;
  pthread_join(sender, NULL);
  uint64_t rest = (uint64_t)remaining.tv_sec * 1000000000 + remaining.tv_nsec;
  if (result != -1 || error != EINTR || !received || rest == 0 ||
      rest > 1000000000)
    return 9;
  puts("NANOSLEEP_BOUNDS_PASS");
  return 0;
}
