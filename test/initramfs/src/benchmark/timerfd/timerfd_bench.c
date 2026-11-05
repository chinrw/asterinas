// SPDX-License-Identifier: MPL-2.0

#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/timerfd.h>
#include <time.h>
#include <unistd.h>

#define NS_PER_SECOND 1000000000ULL
#define NS_PER_BATCH 10000000ULL
#define BATCHES_PER_SECOND (NS_PER_SECOND / NS_PER_BATCH)
#define MICRO_DEADLINE_GAP_NS 100000000ULL
#define CHURN_FIRST_ARM_NS 50000000ULL
#define CHURN_REARM_NS 60000000ULL

static volatile sig_atomic_t stop_requested;

enum micro_mode { CANCEL, REARM, ARMED_CLOSE, EXPIRY };

struct micro_result {
	uint64_t arm_ns;
	uint64_t operation_ns;
	uint64_t expiry_read_p99_ns;
	uint64_t expiry_read_max_ns;
};

static void fail(const char *what)
{
	perror(what);
	exit(2);
}

static void invalid(const char *what)
{
	fprintf(stderr, "%s\n", what);
	exit(3);
}

static uint64_t now_ns(void)
{
	struct timespec time;
	if (clock_gettime(CLOCK_MONOTONIC, &time))
		fail("clock_gettime");
	return (uint64_t)time.tv_sec * NS_PER_SECOND + time.tv_nsec;
}

static void stop_signal(int signal_number)
{
	(void)signal_number;
	stop_requested = 1;
}

static void sleep_until(uint64_t deadline_ns)
{
	struct timespec time = { deadline_ns / NS_PER_SECOND,
				 deadline_ns % NS_PER_SECOND };
	int result;
	do {
		result = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &time,
					 NULL);
	} while (result == EINTR && !stop_requested);
	if (result && result != EINTR) {
		errno = result;
		fail("clock_nanosleep");
	}
}

static int make_timer(void)
{
	int fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC | TFD_NONBLOCK);
	if (fd < 0)
		fail("timerfd_create");
	return fd;
}

static void arm(int fd, uint64_t deadline_ns)
{
	struct itimerspec time = {
		.it_value = { deadline_ns / NS_PER_SECOND,
			      deadline_ns % NS_PER_SECOND },
	};
	if (timerfd_settime(fd, TFD_TIMER_ABSTIME, &time, NULL))
		fail("timerfd_settime");
}

static void close_timer(int fd)
{
	if (close(fd))
		fail("close");
}

static int compare_u64(const void *left, const void *right)
{
	uint64_t a = *(const uint64_t *)left;
	uint64_t b = *(const uint64_t *)right;
	return (a > b) - (a < b);
}

static void check_not_readable(int fd)
{
	struct pollfd descriptor = { .fd = fd, .events = POLLIN };
	int result;
	do {
		result = poll(&descriptor, 1, 0);
	} while (result < 0 && errno == EINTR);
	if (result < 0)
		fail("poll");
	if (result || descriptor.revents)
		invalid("timerfd unexpectedly readable after drain");

	uint64_t value;
	ssize_t bytes = read(fd, &value, sizeof(value));
	if (bytes != -1 || errno != EAGAIN)
		invalid("timerfd read after drain did not return EAGAIN");
}

static uint64_t read_expiry(int fd, uint64_t deadline_ns)
{
	if (now_ns() < deadline_ns)
		invalid("timerfd became readable before its deadline");
	uint64_t value;
	ssize_t bytes = read(fd, &value, sizeof(value));
	if (bytes < 0)
		fail("timerfd read");
	if (bytes != (ssize_t)sizeof(value) || value != 1)
		invalid("timerfd expiry count is not exactly one");
	uint64_t read_at_ns = now_ns();
	if (read_at_ns < deadline_ns)
		invalid("timerfd read completed before its deadline");
	return read_at_ns - deadline_ns;
}

/* The earlier live sentinel keeps deferred entries behind it until the
 * foreground phase ends. Every case then drains past both target deadlines. */
static struct micro_result micro_case(unsigned count, enum micro_mode mode)
{
	static const char *names[] = { "cancel", "rearm", "armed-close",
				       "expiry" };
	int *fds = calloc(count, sizeof(*fds));
	struct pollfd *polls = calloc(count, sizeof(*polls));
	uint64_t *lateness_ns = calloc(count, sizeof(*lateness_ns));
	if (!fds || !polls || !lateness_ns)
		fail("calloc");

	uint64_t start_ns = now_ns();
	uint64_t sentinel_at_ns = start_ns + 2 * NS_PER_SECOND;
	uint64_t old_at_ns = sentinel_at_ns + MICRO_DEADLINE_GAP_NS;
	uint64_t new_at_ns = old_at_ns + MICRO_DEADLINE_GAP_NS;
	uint64_t drain_at_ns = new_at_ns + MICRO_DEADLINE_GAP_NS;
	int sentinel = make_timer();
	arm(sentinel, sentinel_at_ns);
	uint64_t create_start_ns = now_ns();
	for (unsigned i = 0; i < count; i++)
		fds[i] = make_timer();
	uint64_t arm_start_ns = now_ns();
	for (unsigned i = 0; i < count; i++)
		arm(fds[i], old_at_ns);
	uint64_t operation_start_ns = now_ns();
	for (unsigned i = 0; i < count; i++) {
		switch (mode) {
		case CANCEL:
			arm(fds[i], 0);
			break;
		case REARM:
			arm(fds[i], new_at_ns);
			break;
		case ARMED_CLOSE:
			close_timer(fds[i]);
			break;
		case EXPIRY:
			break;
		}
	}
	uint64_t foreground_end_ns = now_ns();
	if (foreground_end_ns >= sentinel_at_ns)
		invalid("foreground phase crossed the sentinel deadline");

	unsigned expirations = 0;
	uint64_t actual_deadline_ns = mode == REARM ? new_at_ns : old_at_ns;
	if (mode == REARM || mode == EXPIRY) {
		for (unsigned i = 0; i < count; i++)
			polls[i] = (struct pollfd){ .fd = fds[i],
						    .events = POLLIN };
		while (expirations < count) {
			if (now_ns() > new_at_ns + 10 * NS_PER_SECOND)
				invalid("timerfd expiry timeout");
			int result = poll(polls, count, 100);
			if (result < 0) {
				if (errno == EINTR)
					continue;
				fail("poll");
			}
			for (unsigned i = 0; i < count; i++) {
				if (polls[i].revents &
				    (POLLERR | POLLHUP | POLLNVAL))
					invalid("unexpected timerfd poll error");
				if (!(polls[i].revents & POLLIN))
					continue;
				lateness_ns[expirations++] =
					read_expiry(fds[i], actual_deadline_ns);
				polls[i].fd = -1;
			}
		}
	}

	sleep_until(drain_at_ns);
	uint64_t drained_ns = now_ns();
	if (drained_ns < drain_at_ns)
		invalid("drain woke before its deadline");
	unsigned nonreadable = 0;
	if (mode != ARMED_CLOSE) {
		for (unsigned i = 0; i < count; i++) {
			check_not_readable(fds[i]);
			nonreadable++;
		}
	}
	(void)read_expiry(sentinel, sentinel_at_ns);
	check_not_readable(sentinel);
	uint64_t close_start_ns = now_ns();
	if (mode != ARMED_CLOSE) {
		for (unsigned i = 0; i < count; i++)
			close_timer(fds[i]);
	}
	close_timer(sentinel);
	uint64_t done_ns = now_ns();
	qsort(lateness_ns, expirations, sizeof(*lateness_ns), compare_u64);
	struct micro_result result = {
		.arm_ns = operation_start_ns - arm_start_ns,
		.operation_ns = foreground_end_ns - operation_start_ns,
		.expiry_read_p99_ns =
			expirations ?
				lateness_ns[(expirations * 99 - 1) / 100] :
				0,
		.expiry_read_max_ns =
			expirations ? lateness_ns[expirations - 1] : 0,
	};
	printf("TIMERFD_CASE {\"n\":%u,\"mode\":\"%s\","
	       "\"create_ns\":%" PRIu64 ",\"arm_ns\":%" PRIu64 ","
	       "\"operation_ns\":%" PRIu64 ",\"foreground_ns\":%" PRIu64 ","
	       "\"close_ns\":%" PRIu64 ",\"total_wall_ns\":%" PRIu64 ","
	       "\"drain_overshoot_ns\":%" PRIu64 ",\"expirations\":%u,"
	       "\"nonreadable_after_drain\":%u,\"closed\":%u,"
	       "\"expiry_read_p99_ns\":%" PRIu64
	       ",\"expiry_read_max_ns\":%" PRIu64 "}\n",
	       count, names[mode], arm_start_ns - create_start_ns,
	       result.arm_ns, result.operation_ns, foreground_end_ns - start_ns,
	       done_ns - close_start_ns, done_ns - start_ns,
	       drained_ns - drain_at_ns, expirations, nonreadable, count,
	       result.expiry_read_p99_ns, result.expiry_read_max_ns);
	free(fds);
	free(polls);
	free(lateness_ns);
	return result;
}

static void prepare_fd_limit(unsigned count)
{
	struct rlimit limit;
	if (getrlimit(RLIMIT_NOFILE, &limit))
		fail("getrlimit");
	/* The sentinel and standard descriptors also occupy slots. */
	rlim_t required = count + 4;
	if (limit.rlim_cur >= required)
		return;
	limit.rlim_cur = required;
	if (limit.rlim_max < required)
		limit.rlim_max = required;
	if (setrlimit(RLIMIT_NOFILE, &limit))
		fail("setrlimit");
}

static void micro(unsigned count)
{
	prepare_fd_limit(count);
	struct micro_result cancel = micro_case(count, CANCEL);
	struct micro_result rearm = micro_case(count, REARM);
	struct micro_result armed_close = micro_case(count, ARMED_CLOSE);
	struct micro_result expiry = micro_case(count, EXPIRY);

	printf("TIMERFD_ARM_AVG_NS %.3f\n", (double)cancel.arm_ns / count);
	printf("TIMERFD_CANCEL_AVG_NS %.3f\n",
	       (double)cancel.operation_ns / count);
	printf("TIMERFD_REARM_AVG_NS %.3f\n",
	       (double)rearm.operation_ns / count);
	printf("TIMERFD_ARMED_CLOSE_AVG_NS %.3f\n",
	       (double)armed_close.operation_ns / count);
	printf("TIMERFD_EXPIRY_READ_P99_NS %" PRIu64 "\n",
	       expiry.expiry_read_p99_ns);
	printf("TIMERFD_EXPIRY_READ_MAX_NS %" PRIu64 "\n",
	       expiry.expiry_read_max_ns);
}

static int churn(unsigned rate, unsigned max_seconds)
{
	enum { POPULATION = 128 };
	int fds[POPULATION];
	struct sigaction action = { .sa_handler = stop_signal };
	if (sigemptyset(&action.sa_mask) || sigaction(SIGTERM, &action, NULL) ||
	    sigaction(SIGINT, &action, NULL))
		fail("sigaction");
	for (unsigned i = 0; i < POPULATION; i++)
		fds[i] = make_timer();

	uint64_t start_ns = now_ns();
	uint64_t stop_at_ns = start_ns + (uint64_t)max_seconds * NS_PER_SECOND;
	uint64_t cycles = 0, skipped_slots = 0, slot = 0;
	printf("TIMERFD_CHURN_STARTED {\"pid\":%d,\"start_ns\":%" PRIu64
	       ",\"rate\":%u}\n",
	       getpid(), start_ns, rate);
	fflush(stdout);
	while (!stop_requested && now_ns() < stop_at_ns) {
		uint64_t current_slot = (now_ns() - start_ns) / NS_PER_BATCH;
		if (current_slot > slot) {
			skipped_slots += current_slot - slot;
			slot = current_slot;
		}
		for (unsigned j = 0;
		     j < rate / BATCHES_PER_SECOND && !stop_requested; j++) {
			unsigned i = cycles % POPULATION;
			uint64_t cycle_ns = now_ns();
			if (cycle_ns >= stop_at_ns)
				break;
			arm(fds[i], cycle_ns + CHURN_FIRST_ARM_NS);
			arm(fds[i], now_ns() + CHURN_REARM_NS);
			if (cycles % 4 == 0) {
				close_timer(fds[i]);
				fds[i] = make_timer();
			} else {
				arm(fds[i], 0);
			}
			cycles++;
		}
		sleep_until(start_ns + (++slot) * NS_PER_BATCH);
	}
	uint64_t finish_ns = now_ns();
	uint64_t elapsed_ns = finish_ns - start_ns;
	for (unsigned i = 0; i < POPULATION; i++)
		close_timer(fds[i]);
	int underloaded = !elapsed_ns ||
			  (long double)cycles * NS_PER_SECOND <
				  (long double)elapsed_ns * rate * 0.9L;
	if (!stop_requested)
		fprintf(stderr,
			"churn reached its time limit without a stop request\n");
	if (underloaded)
		fprintf(stderr,
			"churn achieved less than 90%% of the requested cycle rate\n");
	printf("TIMERFD_CHURN_FINISHED {\"pid\":%d,\"start_ns\":%" PRIu64 ","
	       "\"finish_ns\":%" PRIu64 ",\"elapsed_ns\":%" PRIu64 ","
	       "\"cycles\":%" PRIu64 ",\"skipped_slots\":%" PRIu64 ","
	       "\"stop_requested\":%s,\"rate\":%u}\n",
	       getpid(), start_ns, finish_ns, elapsed_ns, cycles, skipped_slots,
	       stop_requested ? "true" : "false", rate);
	fflush(stdout);
	return !stop_requested || underloaded ? 3 : 0;
}

static unsigned parse_unsigned(const char *text, unsigned maximum)
{
	char *end;
	errno = 0;
	unsigned long value = strtoul(text, &end, 10);
	if (!*text || *text == '-' || *end || errno || !value ||
	    value > maximum) {
		fprintf(stderr, "invalid positive integer: %s\n", text);
		exit(2);
	}
	return value;
}

int main(int argc, char **argv)
{
	if (argc == 3 && !strcmp(argv[1], "micro")) {
		micro(parse_unsigned(argv[2], 4096));
		return 0;
	}
	if (argc == 4 && !strcmp(argv[1], "churn")) {
		unsigned rate = parse_unsigned(argv[2], UINT_MAX);
		unsigned max_seconds = parse_unsigned(argv[3], INT_MAX);
		if (rate < BATCHES_PER_SECOND || rate % BATCHES_PER_SECOND) {
			fprintf(stderr,
				"churn rate must be a positive multiple of %llu\n",
				BATCHES_PER_SECOND);
			return 2;
		}
		return churn(rate, max_seconds);
	}
	fprintf(stderr,
		"usage: timerfd-bench micro N | churn RATE MAX_SECONDS\n");
	return 2;
}
