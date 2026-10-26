// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
static atomic_int ready, stop;
static atomic_ulong sleep_calls;
static long sleep_ns;
static void *sleeper(void *unused) {
    (void)unused;
    atomic_fetch_add(&ready, 1);
    struct timespec duration = { .tv_sec = 0, .tv_nsec = sleep_ns };
    while (!atomic_load(&stop)) {
        long rc = syscall(SYS_nanosleep, &duration, NULL);
        if (rc && errno != EINTR) _exit(3);
        atomic_fetch_add(&sleep_calls, 1);
    }
    return NULL;
}
static void *spinner(void *unused) {
    (void)unused;
    atomic_fetch_add(&ready, 1);
    while (!atomic_load(&stop)) __asm__ volatile("" ::: "memory");
    return NULL;
}
static int64_t now(void) {
    struct timespec t;
    if (syscall(SYS_clock_gettime, CLOCK_MONOTONIC, &t)) _exit(4);
    return (int64_t)t.tv_sec*1000000000+t.tv_nsec;
}
int main(int argc, char **argv) {
    if (argc != 5) return 2;
    int sleepers=atoi(argv[1]), spinners=atoi(argv[2]), iterations=atoi(argv[4]);
    sleep_ns=atol(argv[3]);
    if (sleepers<0 || spinners<0 || sleepers+spinners>32 || sleep_ns<1 || sleep_ns>100000000) return 2;
    pthread_t threads[32];
    for (int i=0; i<sleepers+spinners; i++) {
        int rc=pthread_create(&threads[i],NULL,i<sleepers?sleeper:spinner,NULL);
        if (rc) { printf("CREATE_FAILED %d\n",rc); return 3; }
    }
    while (atomic_load(&ready)<sleepers+spinners) sched_yield();
    int64_t begin=now();
    printf("ALLOC_BEGIN sleepers=%d spinners=%d sleep_ns=%ld\n",sleepers,spinners,sleep_ns); fflush(stdout);
    for (int i=0; i<iterations; i++) {
        volatile char *memory=mmap(NULL,32768,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
        if (memory==MAP_FAILED) { perror("mmap"); return 5; }
        for (int page=0;page<8;page++) memory[page*4096]=1;
        if (munmap((void *)memory,32768)) { perror("munmap"); return 6; }
        if ((i+1)%100==0) {
            printf("ALLOC_PROGRESS iterations=%d elapsed_ns=%ld sleep_calls=%lu\n",i+1,(long)(now()-begin),atomic_load(&sleep_calls)); fflush(stdout);
        }
    }
    atomic_store(&stop,1);
    for (int i=0;i<sleepers+spinners;i++) pthread_join(threads[i],NULL);
    printf("ALLOC_PASS elapsed_ns=%ld sleep_calls=%lu\n",(long)(now()-begin),atomic_load(&sleep_calls)); fflush(stdout);
    return 0;
}
