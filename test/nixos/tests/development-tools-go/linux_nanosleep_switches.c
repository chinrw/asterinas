// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
static atomic_int start, stop, ready;
static long ns;
struct result {
  long vcsw, ivcsw;
};
static void *sleeper(void *arg) {
  struct result *r = arg;
  atomic_fetch_add(&ready, 1);
  while (!atomic_load(&start))
    sched_yield();
  struct rusage before, after;
  struct timespec duration = {.tv_sec = 0, .tv_nsec = ns};
  if (getrusage(RUSAGE_THREAD, &before))
    _exit(3);
  for (int i = 0; i < 1000; i++)
    if (syscall(SYS_nanosleep, &duration, NULL))
      _exit(4);
  if (getrusage(RUSAGE_THREAD, &after))
    _exit(5);
  r->vcsw = after.ru_nvcsw - before.ru_nvcsw;
  r->ivcsw = after.ru_nivcsw - before.ru_nivcsw;
  return NULL;
}
static void *spin(void *arg) {
  (void)arg;
  while (!atomic_load(&stop))
    __asm__ volatile("" ::: "memory");
  return NULL;
}
int main(int argc, char **argv) {
  if (argc != 2)
    return 2;
  ns = atol(argv[1]);
  pthread_t threads[11];
  struct result results[9];
  for (int i = 0; i < 2; i++)
    if (pthread_create(&threads[i], NULL, spin, NULL))
      return 6;
  for (int i = 0; i < 9; i++)
    if (pthread_create(&threads[i + 2], NULL, sleeper, &results[i]))
      return 7;
  while (atomic_load(&ready) < 9)
    sched_yield();
  atomic_store(&start, 1);
  for (int i = 0; i < 9; i++)
    pthread_join(threads[i + 2], NULL);
  atomic_store(&stop, 1);
  for (int i = 0; i < 2; i++)
    pthread_join(threads[i], NULL);
  for (int i = 0; i < 9; i++)
    printf("LINUX_SLEEP_SWITCHES ns=%ld worker=%d calls=1000 voluntary=%ld "
           "involuntary=%ld\n",
           ns, i, results[i].vcsw, results[i].ivcsw);
  return 0;
}
