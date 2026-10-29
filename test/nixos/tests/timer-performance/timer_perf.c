// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/timerfd.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stopped;
static uint64_t now_ns(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) { perror("clock_gettime"); exit(2); }
    return (uint64_t)t.tv_sec * 1000000000ULL + t.tv_nsec;
}
static void fail(const char *what) { perror(what); exit(2); }
static void stop_signal(int sig) { (void)sig; stopped = 1; }
static void sleep_until(uint64_t deadline)
{
    struct timespec t = { deadline / 1000000000ULL, deadline % 1000000000ULL };
    int r;
    do { r = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &t, NULL); }
    while (r == EINTR && !stopped);
    if (r && r != EINTR) { errno = r; fail("clock_nanosleep"); }
}
static int make_timer(void)
{
    int fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC | TFD_NONBLOCK);
    if (fd < 0) fail("timerfd_create");
    return fd;
}
static void arm(int fd, uint64_t deadline, uint64_t interval)
{
    struct itimerspec t = {
        .it_value = { deadline / 1000000000ULL, deadline % 1000000000ULL },
        .it_interval = { interval / 1000000000ULL, interval % 1000000000ULL },
    };
    if (timerfd_settime(fd, TFD_TIMER_ABSTIME, &t, NULL)) fail("timerfd_settime");
}
static void close_timer(int fd) { if (close(fd)) fail("close"); }
static int compare_u64(const void *a, const void *b)
{
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return (x > y) - (x < y);
}

/* A live earlier sentinel prevents cancelled entries being swept before the
 * foreground phase finishes. Each trial then waits beyond every old expiry. */
static void micro(int n, const char *mode)
{
    int *fds = calloc(n, sizeof(*fds));
    struct pollfd *polls = calloc(n, sizeof(*polls));
    uint64_t *lateness = calloc(n, sizeof(*lateness));
    if (!fds || !polls || !lateness) fail("calloc");
    uint64_t start = now_ns(), sentinel_at = start + 2000000000ULL;
    uint64_t old_at = sentinel_at + 100000000ULL, new_at = old_at + 100000000ULL;
    int sentinel = make_timer();
    arm(sentinel, sentinel_at, 0);
    uint64_t a = now_ns();
    for (int i = 0; i < n; i++) fds[i] = make_timer();
    uint64_t b = now_ns();
    for (int i = 0; i < n; i++) arm(fds[i], old_at, 0);
    uint64_t c = now_ns();
    for (int i = 0; i < n; i++) {
        if (!strcmp(mode, "cancel")) arm(fds[i], 0, 0);
        else if (!strcmp(mode, "rearm")) arm(fds[i], new_at, 0);
        else if (!strcmp(mode, "drop")) close_timer(fds[i]);
        else if (strcmp(mode, "expiry")) { fprintf(stderr, "invalid mode\n"); exit(2); }
    }
    uint64_t d = now_ns();
    if (d >= sentinel_at) { fprintf(stderr, "foreground crossed sentinel deadline\n"); exit(3); }
    printf("PERF_FOREGROUND {\"n\":%d,\"mode\":\"%s\",\"create_ns\":%"PRIu64",\"arm_ns\":%"PRIu64",\"operation_ns\":%"PRIu64",\"foreground_ns\":%"PRIu64"}\n",
           n, mode, b-a, c-b, d-c, d-start);
    fflush(stdout);
    uint64_t actual = !strcmp(mode, "rearm") ? new_at : old_at;
    int count = 0;
    if (!strcmp(mode, "expiry") || !strcmp(mode, "rearm")) {
        for (int i = 0; i < n; i++) polls[i] = (struct pollfd){ fds[i], POLLIN, 0 };
        while (count < n) {
            if (now_ns() > new_at + 10000000000ULL) { fprintf(stderr, "expiry timeout\n"); exit(3); }
            int r = poll(polls, n, 100);
            if (r < 0 && errno != EINTR) fail("poll");
            for (int i = 0; i < n; i++) if (polls[i].revents & POLLIN) {
                uint64_t value;
                if (read(fds[i], &value, sizeof(value)) != sizeof(value) || value != 1) fail("timerfd_read");
                uint64_t t = now_ns();
                if (t < actual) { fprintf(stderr, "early expiry\n"); exit(3); }
                lateness[count++] = t - actual;
                polls[i].fd = -1;
            }
        }
    }
    /* Include the base's delayed queue work in the observed trial interval. */
    sleep_until(new_at + 100000000ULL);
    uint64_t close_start = now_ns();
    if (close_start < new_at + 100000000ULL) { fprintf(stderr,"early drain wake\n"); exit(3); }
    if (strcmp(mode, "drop")) for (int i = 0; i < n; i++) close_timer(fds[i]);
    close_timer(sentinel);
    uint64_t done = now_ns();
    qsort(lateness, count, sizeof(*lateness), compare_u64);
    printf("PERF_MICRO {\"n\":%d,\"mode\":\"%s\",\"create_ns\":%"PRIu64",\"arm_ns\":%"PRIu64",\"operation_ns\":%"PRIu64",\"close_ns\":%"PRIu64",\"total_wall_ns\":%"PRIu64",\"drain_overshoot_ns\":%"PRIu64",\"expirations\":%d,\"expiry_read_p99_ns\":%"PRIu64",\"expiry_read_max_ns\":%"PRIu64"}\n",
           n, mode, b-a, c-b, d-c, done-close_start, done-start,
           (uint64_t)(close_start-(new_at+100000000ULL)), count,
           count ? lateness[(count*99-1)/100] : 0, count ? lateness[count-1] : 0);
    free(fds); free(polls); free(lateness);
}

static void load(const char *kind, unsigned rate, unsigned limit_s)
{
    enum { N = 128 };
    int fds[N]; struct pollfd polls[N];
    for (int i = 0; i < N; i++) { fds[i] = make_timer(); polls[i] = (struct pollfd){ fds[i], POLLIN, 0 }; }
    signal(SIGTERM, stop_signal);
    signal(SIGINT, stop_signal);
    uint64_t start = now_ns(), stop_at = start + (uint64_t)limit_s*1000000000ULL;
    uint64_t cycles = 0, expirations = 0, skipped = 0, slot = 0;
    if (!strcmp(kind, "expiry")) {
        for (int i = 0; i < N; i++) arm(fds[i], start+20000000ULL, 20000000ULL);
    } else if (strcmp(kind, "churn") || rate < 100 || rate % 100) {
        fprintf(stderr, "invalid load arguments\n"); exit(2);
    }
    printf("PERF_LOAD_STARTED {\"kind\":\"%s\",\"pid\":%d,\"start_ns\":%"PRIu64",\"rate\":%u,\"population\":%d}\n",kind,getpid(),start,rate,N);
    fflush(stdout);
    while (!stopped && now_ns() < stop_at) {
        if (!strcmp(kind, "expiry")) {
            int r = poll(polls, N, 100);
            if (r < 0 && errno != EINTR) fail("load poll");
            for (int i = 0; i < N; i++) if (polls[i].revents & POLLIN) {
                uint64_t value;
                ssize_t got = read(fds[i], &value, sizeof(value));
                if (got == sizeof(value)) expirations += value;
                else if (errno != EAGAIN) fail("load read");
            }
            cycles++;
        } else {
            uint64_t t = now_ns();
            uint64_t current_slot = (t-start)/10000000ULL;
            if (current_slot > slot) { skipped += current_slot-slot; slot = current_slot; }
            for (unsigned j = 0; j < rate/100 && !stopped; j++) {
                unsigned i = cycles % N;
                arm(fds[i], now_ns()+50000000ULL, 0);
                arm(fds[i], now_ns()+60000000ULL, 0);
                if (cycles % 4 == 0) { close_timer(fds[i]); fds[i] = make_timer(); }
                else arm(fds[i], 0, 0);
                cycles++;
            }
            sleep_until(start + (++slot)*10000000ULL);
        }
    }
    uint64_t finish = now_ns();
    for (int i = 0; i < N; i++) close_timer(fds[i]);
    sleep_until(now_ns()+100000000ULL);
    printf("PERF_LOAD_FINISHED {\"kind\":\"%s\",\"pid\":%d,\"start_ns\":%"PRIu64",\"finish_ns\":%"PRIu64",\"elapsed_ns\":%"PRIu64",\"cycles\":%"PRIu64",\"expirations\":%"PRIu64",\"skipped_slots\":%"PRIu64",\"stop_requested\":%s}\n",
           kind,getpid(),start,finish,finish-start,cycles,expirations,skipped,stopped?"true":"false");
    if (!stopped) exit(3);
}

static void probe(unsigned seconds)
{
    size_t cap = (size_t)seconds * 1000000, count = 0;
    uint64_t *gaps = calloc(cap, sizeof(*gaps));
    if (!gaps) fail("probe calloc");
    uint64_t start = now_ns(), end = start+(uint64_t)seconds*1000000000ULL, prev=start;
    volatile uint64_t value = 1;
    while (prev < end) {
        for (int i = 0; i < 4096; i++) value = value*6364136223846793005ULL+1;
        uint64_t t = now_ns();
        if (count >= cap) { fprintf(stderr,"probe buffer full\n"); exit(3); }
        gaps[count++] = t-prev; prev=t;
    }
    qsort(gaps,count,sizeof(*gaps),compare_u64);
    printf("PERF_PROBE {\"chunks\":%zu,\"operations\":%zu,\"elapsed_ns\":%"PRIu64",\"gap_p99_ns\":%"PRIu64",\"gap_max_ns\":%"PRIu64",\"checksum\":%"PRIu64"}\n",
           count,count*4096,prev-start,gaps[(count*99-1)/100],gaps[count-1],value);
    free(gaps);
}
static void wake_probe(unsigned seconds)
{
    size_t count = seconds*200;
    uint64_t *lateness = calloc(count,sizeof(*lateness));
    if (!lateness) fail("wake calloc");
    uint64_t start = now_ns();
    for (size_t i=0; i<count; i++) {
        uint64_t target=start+(i+1)*5000000ULL;
        sleep_until(target);
        uint64_t actual=now_ns();
        if (actual<target) { fprintf(stderr,"early wake\n"); exit(3); }
        lateness[i]=actual-target;
    }
    qsort(lateness,count,sizeof(*lateness),compare_u64);
    printf("PERF_WAKE {\"samples\":%zu,\"period_ns\":5000000,\"p50_ns\":%"PRIu64",\"p99_ns\":%"PRIu64",\"max_ns\":%"PRIu64"}\n",
           count,lateness[(count-1)/2],lateness[(count*99-1)/100],lateness[count-1]);
    free(lateness);
}
int main(int argc, char **argv)
{
    if (argc==4 && !strcmp(argv[1],"micro")) {
        int n=atoi(argv[2]); if (n<1 || n>4096) return 2; micro(n,argv[3]);
    } else if (argc==5 && !strcmp(argv[1],"load")) load(argv[2],strtoul(argv[3],NULL,10),strtoul(argv[4],NULL,10));
    else if (argc==3 && !strcmp(argv[1],"probe")) { unsigned s=atoi(argv[2]); if (!s || s>60) return 2; probe(s); }
    else if (argc==3 && !strcmp(argv[1],"wake")) { unsigned s=atoi(argv[2]); if (!s || s>60) return 2; wake_probe(s); }
    else if (argc==3 && !strcmp(argv[1],"stamp")) printf("PERF_STAMP {\"label\":\"%s\",\"now_ns\":%"PRIu64"}\n",argv[2],now_ns());
    else { fprintf(stderr,"usage: timer-perf micro N cancel|rearm|drop|expiry | load churn|expiry rate limit_s | probe seconds\n"); return 2; }
    return 0;
}
