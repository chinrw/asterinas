// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <errno.h>
#include <linux/futex.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

static atomic_int started;
static int futex_word;
static void *spin(void *unused) {
    (void)unused;
    atomic_fetch_add(&started, 1);
    for (;;) { __asm__ volatile("" ::: "memory"); }
    return NULL;
}
static int64_t now_ns(void) {
    struct timespec now;
    if (syscall(SYS_clock_gettime, CLOCK_MONOTONIC, &now)) exit(2);
    return (int64_t)now.tv_sec * 1000000000 + now.tv_nsec;
}
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    int threads = atoi(argv[1]), iterations = atoi(argv[2]);
    for (int i = 0; i < threads; i++) {
        pthread_t tid;
        int rc = pthread_create(&tid, NULL, spin, NULL);
        if (rc) { printf("CREATE_FAILED %d\n", rc); return 3; }
    }
    while (atomic_load(&started) < threads) sched_yield();
    printf("THREADS_READY %d\n", threads); fflush(stdout);
    for (int mode = 0; mode < 2; mode++) {
        int64_t begin = now_ns(), maximum = 0;
        printf("WAIT_MODE_BEGIN %d\n", mode); fflush(stdout);
        for (int i = 0; i < iterations; i++) {
            struct timespec duration = { .tv_sec = 0, .tv_nsec = 20000 };
            int64_t before = now_ns();
            long result = mode == 0
                ? syscall(SYS_nanosleep, &duration, NULL)
                : syscall(SYS_futex, &futex_word, FUTEX_WAIT_PRIVATE, 0, &duration, NULL, 0);
            if ((mode == 0 && result != 0 && errno != EINTR) ||
                (mode == 1 && !(result == 0 || (result == -1 && (errno == ETIMEDOUT || errno == EINTR || errno == EAGAIN))))) {
                printf("WAIT_ERROR mode=%d result=%ld errno=%d\n", mode, result, errno);
                return 4;
            }
            int64_t elapsed = now_ns() - before;
            if (elapsed > maximum) maximum = elapsed;
        }
        printf("WAIT_MODE_DONE mode=%d iterations=%d total_ns=%ld maximum_ns=%ld\n",
               mode, iterations, (long)(now_ns()-begin), (long)maximum);
        fflush(stdout);
    }
    puts("WAIT_PROBE_PASS"); fflush(stdout);
    return 0;
}
