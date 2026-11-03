// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <endian.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <netinet/in.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
#include <x86intrin.h>

#define CONTROL 0x41534250
#define MAGIC UINT64_C(0x4153544241544348)
#define VERSION 3
#define HWORDS 32
#define EWORDS 34
#define CAPACITY 128
#define TIMER_WORDS (HWORDS + EWORDS * CAPACITY)
#define HEAP_MAGIC UINT64_C(0x4153544845415044)
#define HEAP_HWORDS 16
#define HEAP_EWORDS 16
#define HEAP_CAPACITY 128
#define BLOB_WORDS (TIMER_WORDS + HEAP_HWORDS + HEAP_EWORDS * HEAP_CAPACITY)
#define BLOB_BYTES (BLOB_WORDS * sizeof(uint64_t))

static uint64_t info[8];
static uint64_t blob[BLOB_WORDS];
static uint64_t threshold_ns;

static int get_info(void)
{
    long result = prctl(CONTROL, 0UL, (unsigned long)info, sizeof(info), 0UL);
    if (result < 0)
        return errno;
    if (result != sizeof(info) || info[0] != MAGIC || info[1] != VERSION ||
        info[2] != HWORDS || info[3] != EWORDS || info[4] != CAPACITY || !info[5])
        return EPROTO;
    return 0;
}

static int monotonic_ns(uint64_t *value)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now))
        return errno ? errno : EIO;
    *value = (uint64_t)now.tv_sec * UINT64_C(1000000000) + now.tv_nsec;
    return 0;
}

static int write_dump(uint64_t case_id)
{
    char path[128], temporary[160];
    snprintf(path, sizeof(path), "/var/tmp/timer-batch-%" PRIu64 ".bin", case_id);
    snprintf(temporary, sizeof(temporary), "%s.%ld.tmp", path, (long)getpid());
    int fd = open(temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (fd < 0)
        return errno;
    int error = 0;
    size_t offset = 0;
    while (offset < BLOB_BYTES) {
        ssize_t count = write(fd, (const char *)blob + offset, BLOB_BYTES - offset);
        if (count <= 0) {
            error = count == 0 ? EIO : errno;
            break;
        }
        offset += count;
    }
    if (!error && fsync(fd))
        error = errno;
    if (close(fd) && !error)
        error = errno;
    if (!error && rename(temporary, path))
        error = errno;
    if (error)
        unlink(temporary);
    return error;
}

static int busy_pause(uint64_t deadline, uint64_t *retries)
{
    uint64_t now;
    int error = monotonic_ns(&now);
    if (error)
        return error;
    if (now >= deadline)
        return ETIMEDOUT;
    ++*retries;
    const struct timespec pause = {.tv_sec = 0, .tv_nsec = 1000000};
    return nanosleep(&pause, NULL) ? errno : 0;
}

static int freeze_dump(uint64_t case_id, uint64_t *retries)
{
    uint64_t now;
    int error = monotonic_ns(&now);
    if (error)
        return error;
    uint64_t deadline = now + UINT64_C(2000000000);
    long result;
    for (;;) {
        result = prctl(CONTROL, 2UL, (unsigned long)case_id, 0UL, 0UL);
        if (result >= 0)
            break;
        error = errno;
        if (error != EBUSY)
            return error;
        if ((error = busy_pause(deadline, retries)))
            return error;
    }
    if (result != BLOB_BYTES)
        return EPROTO;
    for (;;) {
        result = prctl(CONTROL, 3UL, (unsigned long)blob, sizeof(blob), 0UL);
        if (result >= 0)
            break;
        error = errno;
        if (error != EBUSY)
            return error;
        if ((error = busy_pause(deadline, retries)))
            return error;
    }
    if (result != BLOB_BYTES || blob[0] != MAGIC || blob[1] != VERSION ||
        blob[2] != HWORDS || blob[3] != EWORDS || blob[4] != CAPACITY ||
        blob[7] != case_id || blob[8] != (uint64_t)getpid() || blob[15] != 0 ||
        blob[16] != (threshold_ns ? threshold_ns : UINT64_C(5000000)))
        return EPROTO;
    if (blob[TIMER_WORDS] != HEAP_MAGIC || blob[TIMER_WORDS + 1] != VERSION ||
        blob[TIMER_WORDS + 2] != HEAP_HWORDS || blob[TIMER_WORDS + 3] != HEAP_EWORDS ||
        blob[TIMER_WORDS + 4] != HEAP_CAPACITY)
        return EPROTO;
    return write_dump(case_id);
}

int main(int argc, char **argv)
{
    if (argc != 1) {
        if (argc != 3 || strcmp(argv[1], "--threshold-ns") || !argv[2][0] ||
            strspn(argv[2], "0123456789") != strlen(argv[2])) {
            fprintf(stderr, "usage: %s [--threshold-ns 0..1000000000]\n", argv[0]);
            return 1;
        }
        errno = 0;
        unsigned long long value = strtoull(argv[2], NULL, 10);
        if (errno || value > UINT64_C(1000000000)) {
            fprintf(stderr, "invalid threshold ns: %s\n", argv[2]);
            return 1;
        }
        threshold_ns = value;
    }
    if (geteuid() != 0) {
        fprintf(stderr, "timer batch helper requires root\n");
        return 1;
    }
    int error = get_info();
    if (error) {
        fprintf(stderr, "timer batch ABI unavailable: %s\n", strerror(error));
        return 1;
    }
    int fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        perror("socket");
        return 1;
    }
    struct sockaddr_in local = {.sin_family = AF_INET, .sin_port = htons(9998)};
    if (inet_pton(AF_INET, "10.0.2.15", &local.sin_addr) != 1 ||
        bind(fd, (struct sockaddr *)&local, sizeof(local))) {
        perror("bind 10.0.2.15:9998");
        close(fd);
        return 1;
    }
    printf("TIMER_BATCH_READY version=%d threshold_ns=%" PRIu64 "\n",
           VERSION, threshold_ns ? threshold_ns : UINT64_C(5000000));
    fflush(stdout);
    for (;;) {
        uint64_t wire[5] = {0}, request[5] = {0};
        struct sockaddr_in peer;
        socklen_t peer_size = sizeof(peer);
        ssize_t count = recvfrom(fd, wire, sizeof(wire), MSG_TRUNC,
                                 (struct sockaddr *)&peer, &peer_size);
        if (count < 0) {
            if (errno == EINTR)
                continue;
            perror("recvfrom");
            close(fd);
            return 1;
        }
        uint64_t before = __rdtsc();
        for (size_t i = 0; i < 5; ++i)
            request[i] = le64toh(wire[i]);
        uint64_t retries = 0, dumped = 0;
        error = 0;
        if (count != sizeof(wire)) {
            fprintf(stderr, "unexpected timer batch packet length: %zd (expected %zu)\n",
                    count, sizeof(wire));
            error = EINVAL;
        } else if (request[0] != MAGIC || request[1] != VERSION) {
            error = EPROTO;
        } else if (request[2] <= 1) {
            error = request[4] != 0 ? EINVAL : get_info();
        } else if (request[2] == 2) {
            if (!request[4]) {
                error = EINVAL;
            } else if (prctl(CONTROL, 1UL, (unsigned long)request[4], (unsigned long)threshold_ns, 0UL) < 0) {
                error = errno;
            } else {
                error = get_info();
            }
        } else if (request[2] == 3) {
            error = freeze_dump(request[4], &retries);
            if (!error) {
                dumped = BLOB_BYTES;
                error = get_info();
            }
        } else {
            error = EINVAL;
        }
        uint64_t after = __rdtsc();
        uint64_t reply[12] = {MAGIC, VERSION, request[2], request[3], request[4],
                             (uint64_t)error, before, after, info[5], info[6], dumped, retries};
        if (error)
            fprintf(stderr, "timer batch command=%" PRIu64 " nonce=%" PRIu64
                    " case=%" PRIu64 ": %s\n", request[2], request[3], request[4], strerror(error));
        for (size_t i = 0; i < 12; ++i)
            reply[i] = htole64(reply[i]);
        ssize_t sent = sendto(fd, reply, sizeof(reply), 0, (struct sockaddr *)&peer, peer_size);
        if (sent != sizeof(reply))
            fprintf(stderr, "timer batch reply failed: %s\n", sent < 0 ? strerror(errno) : "short datagram");
    }
}
