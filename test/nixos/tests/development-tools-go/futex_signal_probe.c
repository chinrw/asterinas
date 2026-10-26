// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <errno.h>
#include <linux/futex.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

static atomic_int turn, ready, done;
static atomic_int sent, send_errors;
static volatile sig_atomic_t received;
static int iterations, with_signal;
static pthread_t worker_thread;
static void handler(int unused) { (void)unused; received++; }
static void wait_value(int value, int expected) {
    while (atomic_load(&turn) != value) {
        int result = syscall(SYS_futex, &turn, FUTEX_WAIT_PRIVATE, expected, NULL, NULL, 0);
        if (result < 0 && errno != EAGAIN && errno != EINTR) {
            printf("FUTEX_ERROR %d\n", errno); fflush(stdout); exit(4);
        }
    }
}
static void wake_value(int value) {
    atomic_store(&turn, value);
    if (syscall(SYS_futex, &turn, FUTEX_WAKE_PRIVATE, 1, NULL, NULL, 0) < 0) exit(5);
}
static void *worker(void *unused) {
    (void)unused;
    char signal_stack[32768];
    stack_t stack = { .ss_sp = signal_stack, .ss_size = sizeof(signal_stack) };
    if (sigaltstack(&stack, NULL)) exit(6);
    atomic_store(&ready, 1);
    for (int i = 0; i < iterations; i++) { wait_value(1, 0); wake_value(0); }
    while (!atomic_load(&done)) sched_yield();
    return NULL;
}
static void *sender(void *unused) {
    (void)unused;
    while (!atomic_load(&done)) {
        int result = pthread_kill(worker_thread, SIGURG);
        if (!result) atomic_fetch_add(&sent, 1);
        else if (result != ESRCH) atomic_fetch_add(&send_errors, 1);
        struct timespec delay = { .tv_nsec = 100000 };
        syscall(SYS_nanosleep, &delay, NULL);
    }
    return NULL;
}
int main(int argc, char **argv) {
    if (argc != 4) return 2;
    iterations = atoi(argv[1]); with_signal = atoi(argv[2]);
    struct sigaction action = { .sa_handler = handler, .sa_flags = SA_ONSTACK };
    if (atoi(argv[3])) action.sa_flags |= SA_RESTART;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGURG, &action, NULL)) return 3;
    if (pthread_create(&worker_thread, NULL, worker, NULL)) return 7;
    while (!atomic_load(&ready)) sched_yield();
    pthread_t sender_thread;
    if (with_signal && pthread_create(&sender_thread, NULL, sender, NULL)) return 8;
    printf("PING_BEGIN rounds=%d signal=%d restart=%d\n", iterations, with_signal, atoi(argv[3])); fflush(stdout);
    for (int i = 0; i < iterations; i++) { wake_value(1); wait_value(0, 1); }
    atomic_store(&done, 1);
    puts("HANDSHAKE_DONE"); fflush(stdout);
    pthread_join(worker_thread, NULL);
    if (with_signal) pthread_join(sender_thread, NULL);
    printf("PING_DONE sent=%d received=%d errors=%d\n", atomic_load(&sent), received, atomic_load(&send_errors)); fflush(stdout);
    return atomic_load(&send_errors) ? 9 : 0;
}
