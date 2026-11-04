// SPDX-License-Identifier: MPL-2.0
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <endian.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <x86intrin.h>

#define CONTROL 0x41534351
#define MAGIC UINT64_C(0x41535443454e5355)
#define VERSION 1
#define HWORDS 16
#define CPUS 64
#define KINDS 5
#define FIELDS 128
#define WORDS (HWORDS + CPUS * KINDS * FIELDS)
#define BYTES (WORDS * sizeof(uint64_t))
#define PORT 9997

static uint64_t info[8], blob[WORDS];
static uint64_t active_case, active_mode, epoch, last_case;

static int get_info(void)
{
    long result = prctl(CONTROL, 0UL, (unsigned long)info, sizeof(info), 0UL);
    if (result < 0) return errno;
    if (result != sizeof(info) || info[0] != MAGIC || info[1] != VERSION ||
        info[2] != HWORDS || info[3] != CPUS || info[4] != KINDS ||
        info[5] != FIELDS || !info[6] || info[7] != BYTES) return EPROTO;
    return 0;
}

static int check_blob(uint64_t case_id, uint64_t mode, uint64_t expected_epoch)
{
    if (!case_id || mode > 2 || blob[0] != MAGIC || blob[1] != VERSION || blob[2] != HWORDS ||
        blob[3] != CPUS || blob[4] != KINDS || blob[5] != FIELDS ||
        !blob[6] || !blob[7] || blob[8] != case_id || blob[9] != mode ||
        blob[10] > blob[11] || blob[11] > blob[12] || blob[13] ||
        blob[14] || blob[15] || (expected_epoch && blob[7] != expected_epoch)) return EPROTO;
    return 0;
}

static int json_dump(FILE *stream)
{
    if (fprintf(stream, "{\"header\":[") < 0) return EIO;
    for (size_t i = 0; i < HWORDS; ++i)
        if (fprintf(stream, "%s%" PRIu64, i ? "," : "", blob[i]) < 0) return EIO;
    if (fprintf(stream, "],\"rows\":[") < 0) return EIO;
    int first = 1;
    for (size_t cpu = 0; cpu < CPUS; ++cpu) {
        for (size_t kind = 0; kind < KINDS; ++kind) {
            uint64_t *row = blob + HWORDS + (cpu * KINDS + kind) * FIELDS;
            int nonzero = 0;
            for (size_t field = 0; field < FIELDS; ++field) nonzero |= row[field] != 0;
            if (!nonzero) continue;
            if (fprintf(stream, "%s{\"cpu\":%zu,\"kind\":%zu,\"fields\":{", first ? "" : ",", cpu, kind) < 0) return EIO;
            first = 0;
            int first_field = 1;
            for (size_t field = 0; field < FIELDS; ++field) {
                if (!row[field]) continue;
                if (fprintf(stream, "%s\"%zu\":%" PRIu64, first_field ? "" : ",", field, row[field]) < 0) return EIO;
                first_field = 0;
            }
            if (fprintf(stream, "}}") < 0) return EIO;
        }
    }
    return fprintf(stream, "]}\n") < 0 ? EIO : 0;
}

static int write_one(const char *path, int json)
{
    char temporary[192];
    if (snprintf(temporary, sizeof(temporary), "%s.%ld.tmp", path, (long)getpid()) >= (int)sizeof(temporary)) return ENAMETOOLONG;
    int fd = open(temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (fd < 0) return errno;
    int error = 0;
    if (json) {
        FILE *stream = fdopen(fd, "w");
        if (!stream) { error = errno; close(fd); }
        else {
            error = json_dump(stream);
            if (fflush(stream) && !error) error = errno;
            if (fsync(fd) && !error) error = errno;
            if (fclose(stream) && !error) error = errno;
        }
    } else {
        size_t offset = 0;
        while (offset < BYTES) {
            ssize_t count = write(fd, (char *)blob + offset, BYTES - offset);
            if (count < 0 && errno == EINTR) continue;
            if (count <= 0) { error = count < 0 ? errno : EIO; break; }
            offset += count;
        }
        if (fsync(fd) && !error) error = errno;
        if (close(fd) && !error) error = errno;
    }
    /* Linking publishes a complete file and refuses to replace earlier evidence. */
    if (!error && link(temporary, path)) error = errno;
    unlink(temporary);
    return error;
}

static int case_paths(uint64_t case_id, char *binary, size_t binary_size, char *json, size_t json_size)
{
    if (snprintf(binary, binary_size, "/var/tmp/timer-census-%" PRIu64 ".bin", case_id) >= (int)binary_size ||
        snprintf(json, json_size, "/var/tmp/timer-census-%" PRIu64 ".json", case_id) >= (int)json_size) return ENAMETOOLONG;
    struct stat st;
    if (!lstat(binary, &st)) return EEXIST;
    if (errno != ENOENT) return errno;
    if (!lstat(json, &st)) return EEXIST;
    return errno == ENOENT ? 0 : errno;
}

static int monotonic_ns(uint64_t *value)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return errno ? errno : EIO;
    *value = (uint64_t)now.tv_sec * UINT64_C(1000000000) + now.tv_nsec;
    return 0;
}

static int busy_pause(uint64_t deadline, uint64_t *retries)
{
    uint64_t now;
    int error = monotonic_ns(&now);
    if (error) return error;
    if (now >= deadline) return ETIMEDOUT;
    if (++*retries > 2000) return ETIMEDOUT;
    const struct timespec pause = {.tv_nsec = 1000000};
    return nanosleep(&pause, NULL) && errno != EINTR ? errno : 0;
}

static int freeze_dump(uint64_t case_id, uint64_t mode, uint64_t *retries)
{
    if (!active_case || active_case != case_id || active_mode != mode) return EINVAL;
    uint64_t now;
    int error = monotonic_ns(&now);
    if (error) return error;
    uint64_t deadline = now + UINT64_C(2000000000);
    long result;
    do {
        result = prctl(CONTROL, 2UL, (unsigned long)case_id, 0UL, 0UL);
        if (result >= 0) break;
        if (errno != EBUSY) return errno;
        error = busy_pause(deadline, retries);
        if (error) return error;
    } while (1);
    if (result != BYTES) return EPROTO;
    result = prctl(CONTROL, 3UL, (unsigned long)blob, sizeof(blob), 0UL);
    if (result < 0) return errno;
    if (result != BYTES || (error = check_blob(case_id, mode, epoch)) || blob[6] != info[6]) return error ? error : EPROTO;
    epoch = blob[7];
    active_case = 0;
    char binary[128], json[128];
    if ((error = case_paths(case_id, binary, sizeof(binary), json, sizeof(json)))) return error;
    if ((error = write_one(binary, 0))) return error;
    return write_one(json, 1);
}

static int dump_file(const char *path)
{
    int fd = open(path, O_RDONLY | O_NOFOLLOW);
    struct stat st;
    if (fd < 0) return errno;
    int error = 0;
    if (fstat(fd, &st)) error = errno;
    else if (!S_ISREG(st.st_mode) || st.st_size != BYTES) error = EPROTO;
    size_t offset = 0;
    while (!error && offset < BYTES) {
        ssize_t count = read(fd, (char *)blob + offset, BYTES - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { error = count < 0 ? errno : EIO; break; }
        offset += count;
    }
    close(fd);
    if (!error) error = check_blob(blob[8], blob[9], blob[7]);
    if (!error) error = json_dump(stdout);
    return error;
}

int main(int argc, char **argv)
{
    if (argc == 3 && !strcmp(argv[1], "--dump")) {
        int error = dump_file(argv[2]);
        if (error) fprintf(stderr, "census dump: %s\n", strerror(error));
        return error ? 1 : 0;
    }
    if (argc != 1 || geteuid()) { fprintf(stderr, "usage: timer-census [--dump FILE] (daemon requires root)\n"); return 1; }
    int error = get_info();
    if (error) { fprintf(stderr, "timer census ABI: %s\n", strerror(error)); return 1; }
    int fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) { perror("socket"); return 1; }
    struct sockaddr_in local = {.sin_family = AF_INET, .sin_port = htons(PORT)};
    if (inet_pton(AF_INET, "10.0.2.15", &local.sin_addr) != 1 || bind(fd, (struct sockaddr *)&local, sizeof(local))) { perror("bind 10.0.2.15:9997"); close(fd); return 1; }
    printf("TIMER_CENSUS_READY version=%d pid=%ld bytes=%zu frequency=%" PRIu64 "\n", VERSION, (long)getpid(), BYTES, info[6]);
    fflush(stdout);
    for (;;) {
        uint64_t wire[6] = {0}, request[6] = {0};
        struct sockaddr_in peer;
        socklen_t peer_size = sizeof(peer);
        ssize_t count = recvfrom(fd, wire, sizeof(wire), MSG_TRUNC, (struct sockaddr *)&peer, &peer_size);
        if (count < 0) { if (errno == EINTR) continue; perror("recvfrom"); return 1; }
        uint64_t before = __rdtsc(), retries = 0, dumped = 0;
        for (size_t i = 0; i < 6; ++i) request[i] = le64toh(wire[i]);
        error = 0;
        if (count != sizeof(wire)) error = EINVAL;
        else if (request[0] != MAGIC || request[1] != VERSION) error = EPROTO;
        else if (request[2] <= 1) error = request[4] || request[5] ? EINVAL : get_info();
        else if (request[2] == 2) {
            char binary[128], json[128];
            if (!request[4] || request[4] <= last_case || request[5] > 2 || active_case) error = EINVAL;
            else if ((error = case_paths(request[4], binary, sizeof(binary), json, sizeof(json)))) {}
            else {
                long result = prctl(CONTROL, 1UL, (unsigned long)request[4], (unsigned long)request[5], 0UL);
                if (result < 0) error = errno;
                else if (!result) error = EPROTO;
                else { active_case = last_case = request[4]; active_mode = request[5]; epoch = (uint64_t)result; }
            }
        } else if (request[2] == 3) {
            error = freeze_dump(request[4], request[5], &retries);
            if (!error) dumped = BYTES;
        } else error = EINVAL;
        uint64_t reply[14] = {MAGIC, VERSION, request[2], request[3], request[4], request[5], (uint64_t)error, before, __rdtsc(), info[6], epoch, dumped, retries, (uint64_t)getpid()};
        for (size_t i = 0; i < 14; ++i) reply[i] = htole64(reply[i]);
        if (sendto(fd, reply, sizeof(reply), 0, (struct sockaddr *)&peer, peer_size) != sizeof(reply)) fprintf(stderr, "census reply failed\n");
    }
}
