// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/epoll.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/timerfd.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define CHECK(x)                                                               \
  do {                                                                         \
    if (!(x)) {                                                                \
      fprintf(stderr, "failure line=%d errno=%d: %s\n", __LINE__, errno, #x);  \
      exit(1);                                                                 \
    }                                                                          \
  } while (0)
#define WORKERS 4
#define ROUNDS 2000
static int cpus[WORKERS], workers;
static uint64_t now_ns(void) {
  struct timespec ts;
  CHECK(clock_gettime(CLOCK_MONOTONIC, &ts) == 0);
  return (uint64_t)ts.tv_sec * 1000000000 + ts.tv_nsec;
}
static struct timespec time_value(uint64_t ns) {
  return (struct timespec){.tv_sec = ns / 1000000000,
                           .tv_nsec = ns % 1000000000};
}
static void configure_cpus(int requested) {
  cpu_set_t available;
  CHECK(sched_getaffinity(0, sizeof(available), &available) == 0);
  for (int cpu = 0; cpu < CPU_SETSIZE && workers < WORKERS; cpu++)
    if (CPU_ISSET(cpu, &available))
      cpus[workers++] = cpu;
  CHECK(workers > 0 && requested <= workers);
  if (requested)
    workers = requested;
}
static unsigned pin_current_cpu(void) {
  unsigned cpu, observed;
  CHECK(syscall(SYS_getcpu, &cpu, NULL, NULL) == 0);
  CHECK(cpu < CPU_SETSIZE);
  cpu_set_t set;
  CPU_ZERO(&set);
  CPU_SET(cpu, &set);
  // Keep the current CPU; the tested base does not migrate running tasks.
  CHECK(sched_setaffinity(0, sizeof(set), &set) == 0);
  CHECK(syscall(SYS_getcpu, &observed, NULL, NULL) == 0 && observed == cpu);
  return cpu;
}
static void arm_fd(int fd, uint64_t expires, uint64_t interval, int flags) {
  struct itimerspec value = {.it_value = time_value(expires),
                             .it_interval = time_value(interval)};
  CHECK(timerfd_settime(fd, flags, &value, NULL) == 0);
}
static timer_t posix_timer(clockid_t clock) {
  struct sigevent event = {.sigev_notify = SIGEV_NONE};
  timer_t timer;
  CHECK(timer_create(clock, &event, &timer) == 0);
  return timer;
}
static void arm_posix(timer_t timer, uint64_t expires) {
  struct itimerspec value = {.it_value = time_value(expires)};
  CHECK(timer_settime(timer, 0, &value, NULL) == 0);
}
static pthread_barrier_t start;
static atomic_int active, finished;
static atomic_ulong overlaps, reads;
static atomic_int observed_cpus[CPU_SETSIZE];
static int shared_fd;
static void *worker(void *argument) {
  int index = (int)(intptr_t)argument;
  unsigned actual_cpu = pin_current_cpu();
  atomic_store(&observed_cpus[actual_cpu], 1);
  int fd = dup(shared_fd);
  CHECK(fd >= 0);
  atomic_fetch_add(&active, 1);
  int result = pthread_barrier_wait(&start);
  CHECK(result == 0 || result == PTHREAD_BARRIER_SERIAL_THREAD);
  for (int round = 0; round < ROUNDS; round++) {
    if (atomic_load(&active) > 1)
      atomic_fetch_add(&overlaps, 1);
    if (index % 3 == 0) {
      arm_fd(fd, round % 2 ? 1000 : 0, round % 3 ? 0 : 1000000, 0);
    } else if (index % 3 == 1) {
      arm_fd(fd, now_ns() + 1000, 0, TFD_TIMER_ABSTIME);
    } else {
      uint64_t ticks;
      ssize_t bytes = read(fd, &ticks, sizeof(ticks));
      CHECK(bytes == sizeof(ticks) ||
            (bytes < 0 && (errno == EAGAIN || errno == EINTR)));
      if (bytes == sizeof(ticks)) {
        CHECK(ticks > 0);
        atomic_fetch_add(&reads, 1);
      }
    }
    // The last fd disappears while other CPUs continue dispatching timers.
    int temporary = timerfd_create(CLOCK_MONOTONIC, TFD_NONBLOCK | TFD_CLOEXEC);
    CHECK(temporary >= 0);
    arm_fd(temporary, round % 2 ? 1000 : 10000000000ULL, 0, 0);
    CHECK(close(temporary) == 0);
    timer_t timer =
        posix_timer(round % 8 ? CLOCK_MONOTONIC : CLOCK_PROCESS_CPUTIME_ID);
    arm_posix(timer, round % 2 ? 1000 : 10000000000ULL);
    CHECK(timer_delete(timer) == 0);
  }
  CHECK(close(fd) == 0);
  printf("TIMER_WORKER cpu=%d rounds=%d\n", actual_cpu, ROUNDS);
  atomic_fetch_sub(&active, 1);
  atomic_fetch_add(&finished, 1);
  return NULL;
}
static void stress(void) {
  pthread_t threads[WORKERS];
  CHECK(pthread_barrier_init(&start, NULL, workers) == 0);
  shared_fd = timerfd_create(CLOCK_MONOTONIC, TFD_NONBLOCK | TFD_CLOEXEC);
  int probe = timerfd_create(CLOCK_MONOTONIC, TFD_NONBLOCK | TFD_CLOEXEC);
  CHECK(shared_fd >= 0 && probe >= 0);
  arm_fd(probe, 1000000, 1000000, 0);
  uint64_t begin = now_ns(), last = begin, max_gap = 0, progress = 0;
  for (int i = 0; i < workers; i++)
    CHECK(pthread_create(&threads[i], NULL, worker, (void *)(intptr_t)i) == 0);
  while (atomic_load(&finished) < workers || progress == 0) {
    uint64_t ticks;
    ssize_t bytes = read(probe, &ticks, sizeof(ticks));
    CHECK(bytes == sizeof(ticks) ||
          (bytes < 0 && (errno == EAGAIN || errno == EINTR)));
    uint64_t current = now_ns();
    CHECK(current - begin < 90000000000ULL);
    if (bytes == sizeof(ticks)) {
      CHECK(ticks > 0);
      if (current - last > max_gap)
        max_gap = current - last;
      last = current;
      progress += ticks;
    }
    struct timespec pause = time_value(1000000);
    while (nanosleep(&pause, &pause) && errno == EINTR) {
    }
  }
  for (int i = 0; i < workers; i++)
    CHECK(pthread_join(threads[i], NULL) == 0);
  CHECK(workers == 1 || atomic_load(&overlaps) > 0);
  int distinct = 0;
  for (int cpu = 0; cpu < CPU_SETSIZE; cpu++)
    distinct += atomic_load(&observed_cpus[cpu]);
  CHECK(distinct == workers);
  printf("TIMER_ACTUAL_CPUS count=%d\n", distinct);
  arm_fd(shared_fd, 0, 0, 0);
  CHECK(close(shared_fd) == 0 && close(probe) == 0);
  CHECK(pthread_barrier_destroy(&start) == 0);
  printf("TIMER_STRESS_PASS cpus=%d operations=%d overlap=%lu reads=%lu "
         "probe_ticks=%" PRIu64 " max_observed_read_gap_ns=%" PRIu64
         " elapsed_ns=%" PRIu64 "\n",
         workers, workers * ROUNDS, atomic_load(&overlaps), atomic_load(&reads),
         progress, max_gap, now_ns() - begin);
}
static void teardown(void) {
  for (int round = 0; round < 16; round++) {
    pid_t children[WORKERS];
    for (int i = 0; i < workers; i++) {
      children[i] = fork();
      CHECK(children[i] >= 0);
      if (!children[i]) {
        (void)pin_current_cpu();
        for (int j = 0; j < 64; j++) {
          timer_t timer =
              posix_timer(j % 8 ? CLOCK_MONOTONIC : CLOCK_PROCESS_CPUTIME_ID);
          arm_posix(timer, j % 2 ? 1000 : 10000000000ULL);
          if (j < 16) {
            int fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);
            CHECK(fd >= 0);
            arm_fd(fd, j % 2 ? 1000 : 10000000000ULL, 0, 0);
          }
        }
        // Intentionally rely on process teardown, not explicit delete/close.
        _exit(0);
      }
    }
    for (int i = 0; i < workers; i++) {
      int status;
      CHECK(waitpid(children[i], &status, 0) == children[i]);
      CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    }
  }
  printf("TIMER_TEARDOWN_PASS children=%d posix_timers=%d timerfds=%d\n",
         16 * workers, 16 * workers * 64, 16 * workers * 16);
}
static int compare_u64(const void *a, const void *b) {
  uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
  return (x > y) - (x < y);
}
static void burst_one(int population) {
  int *fds = calloc(population, sizeof(*fds));
  uint64_t *latencies = calloc(population, sizeof(*latencies));
  unsigned char *seen = calloc(population, 1);
  CHECK(fds && latencies && seen);
  int epoll = epoll_create1(EPOLL_CLOEXEC);
  CHECK(epoll >= 0);
  for (int i = 0; i < population; i++) {
    fds[i] = timerfd_create(CLOCK_MONOTONIC, TFD_NONBLOCK | TFD_CLOEXEC);
    CHECK(fds[i] >= 0);
    struct epoll_event event = {.events = EPOLLIN | EPOLLONESHOT,
                                .data.u32 = i};
    CHECK(epoll_ctl(epoll, EPOLL_CTL_ADD, fds[i], &event) == 0);
  }
  uint64_t begin = now_ns(), deadline = begin + 2000000000ULL;
  for (int i = 0; i < population; i++)
    arm_fd(fds[i], deadline, 0, TFD_TIMER_ABSTIME);
  uint64_t armed = now_ns();
  CHECK(armed < deadline);
  int completed = 0;
  while (completed < population) {
    struct epoll_event events[128];
    int count = epoll_wait(epoll, events, 128, 5000);
    CHECK(count > 0);
    for (int i = 0; i < count; i++) {
      unsigned id = events[i].data.u32;
      CHECK(id < (unsigned)population && !seen[id]);
      uint64_t ticks;
      CHECK(read(fds[id], &ticks, sizeof(ticks)) == sizeof(ticks) &&
            ticks == 1);
      uint64_t current = now_ns();
      CHECK(current >= deadline);
      latencies[completed++] = current - deadline;
      seen[id] = 1;
    }
  }
  qsort(latencies, population, sizeof(*latencies), compare_u64);
  printf("TIMER_BURST n=%d arm_ns=%" PRIu64 " read_p50_ns=%" PRIu64
         " read_p99_ns=%" PRIu64 " read_max_ns=%" PRIu64 "\n",
         population, armed - begin, latencies[population / 2],
         latencies[(population - 1) * 99 / 100], latencies[population - 1]);
  begin = now_ns();
  for (int i = 0; i < population; i++)
    arm_fd(fds[i], 3600000000000ULL, 0, 0);
  armed = now_ns();
  for (int i = 0; i < population; i++)
    arm_fd(fds[i], 0, 0, 0);
  uint64_t cancelled = now_ns();
  for (int i = 0; i < population; i++)
    CHECK(close(fds[i]) == 0);
  printf("TIMER_COST n=%d arm_ns=%" PRIu64 " cancel_ns=%" PRIu64
         " close_ns=%" PRIu64 "\n",
         population, armed - begin, cancelled - armed, now_ns() - cancelled);
  CHECK(close(epoll) == 0);
  free(fds);
  free(latencies);
  free(seen);
}
static void burst(void) {
  struct rlimit limit;
  CHECK(getrlimit(RLIMIT_NOFILE, &limit) == 0);
  if (limit.rlim_cur < 8192) {
    limit.rlim_cur = 8192;
    CHECK(setrlimit(RLIMIT_NOFILE, &limit) == 0);
  }
  for (int population = 64; population <= 4096; population *= 4)
    burst_one(population);
  puts("TIMER_BURST_PASS endpoint=userspace_timerfd_read");
}
int main(int argc, char **argv) {
  CHECK(argc == 3);
  configure_cpus(atoi(argv[2]));
  if (!strcmp(argv[1], "stress"))
    stress();
  else if (!strcmp(argv[1], "teardown"))
    teardown();
  else if (!strcmp(argv[1], "burst"))
    burst();
  else
    CHECK(0);
  return 0;
}
