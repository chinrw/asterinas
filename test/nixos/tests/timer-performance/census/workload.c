// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/timerfd.h>
#include <time.h>
#include <unistd.h>

static void fail(const char *operation) { perror(operation); exit(2); }
static uint64_t now_ns(void)
{
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value)) fail("clock_gettime");
    return (uint64_t)value.tv_sec * UINT64_C(1000000000) + value.tv_nsec;
}
static void sleep_until(uint64_t deadline)
{
    struct timespec value = {deadline / UINT64_C(1000000000), deadline % UINT64_C(1000000000)};
    int result;
    do { result = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &value, NULL); } while (result == EINTR);
    if (result) { errno = result; fail("clock_nanosleep"); }
}
static int timer(uint64_t deadline, uint64_t period)
{
    int fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC | TFD_NONBLOCK);
    if (fd < 0) fail("timerfd_create");
    struct itimerspec value = {
        .it_value = {deadline / UINT64_C(1000000000), deadline % UINT64_C(1000000000)},
        .it_interval = {period / UINT64_C(1000000000), period % UINT64_C(1000000000)},
    };
    if (timerfd_settime(fd, TFD_TIMER_ABSTIME, &value, NULL)) fail("timerfd_settime");
    return fd;
}
int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    uint64_t start = now_ns(), deadline = start + UINT64_C(2000000000), expirations = 0;
    if (!strcmp(argv[1], "idle") || !strcmp(argv[1], "future")) {
        int fd = -1;
        if (!strcmp(argv[1], "future")) fd = timer(start + UINT64_C(3600000000000), 0);
        sleep_until(deadline);
        if (fd >= 0 && close(fd)) fail("close future");
    } else if (!strcmp(argv[1], "periodic")) {
        enum { COUNT = 64 };
        struct pollfd fds[COUNT];
        uint64_t counts[COUNT] = {0};
        for (int i = 0; i < COUNT; ++i)
            fds[i] = (struct pollfd){.fd = timer(start + UINT64_C(20000000), UINT64_C(20000000)), .events = POLLIN};
        while (now_ns() < deadline) {
            int result = poll(fds, COUNT, 20);
            if (result < 0) { if (errno == EINTR) continue; fail("poll"); }
            for (int i = 0; i < COUNT; ++i) {
                if (!(fds[i].revents & POLLIN)) continue;
                uint64_t count;
                ssize_t got = read(fds[i].fd, &count, sizeof(count));
                if (got != sizeof(count) || !count) fail("read periodic");
                counts[i] += count;
                expirations += count;
            }
        }
        for (int i = 0; i < COUNT; ++i) {
            if (!counts[i]) { fprintf(stderr, "periodic timer %d never expired\n", i); return 3; }
            if (close(fds[i].fd)) fail("close periodic");
        }
    } else return 2;
    printf("CENSUS_WORKLOAD {\"kind\":\"%s\",\"start_ns\":%" PRIu64 ",\"end_ns\":%" PRIu64 ",\"expirations\":%" PRIu64 "}\n", argv[1], start, now_ns(), expirations);
    return 0;
}
