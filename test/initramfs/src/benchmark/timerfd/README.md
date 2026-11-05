# Timerfd benchmarks

These jobs compare timerfd operation costs and expiry read lateness on Linux and Asterinas.
Run them in the project development environment from the repository root, using the existing benchmark runner:

```sh
bash test/initramfs/src/benchmark/bench_linux_and_aster.sh timerfd/count64_smp1
bash test/initramfs/src/benchmark/bench_linux_and_aster.sh timerfd/count64_smp4
bash test/initramfs/src/benchmark/bench_linux_and_aster.sh timerfd/count4096_smp1
bash test/initramfs/src/benchmark/bench_linux_and_aster.sh timerfd/count4096_smp4
```

Each job runs `/benchmark/bin/timerfd-bench micro N` once.
The helper runs cancel, rearm, armed-close, and expiry cases in that order.
The jobs select N=64 or 4096 and one or four guest CPUs.
The helper uses one foreground thread; SMP changes the guest configuration rather than the number of producer threads.
Before measuring, the helper raises `RLIMIT_NOFILE` to at least N+4 to accommodate the target timers, sentinel, and standard descriptors.
Raising the hard limit requires root or `CAP_SYS_RESOURCE`; the benchmark guests run as root.
N+4 assumes only the three standard descriptors are inherited.
If a caller retains additional descriptors, it must provide a higher limit before invoking the helper.
A denied limit change or `EMFILE` fails the run without reducing N.

Each case arms a live sentinel at start+2s and N one-shot timers at start+2.1s.
Rearm moves those timers to start+2.2s.
The foreground phase must finish before the sentinel deadline.
Each case waits until at least start+2.3s before its final checks and closes.
This keeps deferred queue work inside the trial interval.
The fixed timing is part of the workload; a slow foreground phase fails the run instead of moving its deadlines.

## Metrics and validation

The helper emits these six scalar lines only after all four cases pass:

| Scalar | Definition |
| --- | --- |
| `TIMERFD_ARM_AVG_NS` | Initial arm phase of the cancel case, divided by N. |
| `TIMERFD_CANCEL_AVG_NS` | Cancel phase elapsed time divided by N. |
| `TIMERFD_REARM_AVG_NS` | Rearm phase elapsed time divided by N. |
| `TIMERFD_ARMED_CLOSE_AVG_NS` | Armed close phase elapsed time divided by N. |
| `TIMERFD_EXPIRY_READ_P99_NS` | Nearest-rank P99 time from the deadline to the completed read in the expiry case. |
| `TIMERFD_EXPIRY_READ_MAX_NS` | Maximum time from the deadline to the completed read in the expiry case. |

The averages measure whole stages and are not per-call tail latencies.
Expiry read lateness includes scheduling, polling, scanning ready descriptors, and reading.
It does not isolate timer interrupt or callback latency.
There are no performance pass/fail thresholds in the helper.

Expiry and rearm cases require exactly one expiration per timer and reject readiness or reads observed before the deadline.
After the drain, every open target timer must be nonreadable according to `poll` and return `EAGAIN` on `read`.
This also checks that cancel prevented an expiration.
The sentinel must have exactly one expiration.
Syscall and close failures fail the run.

Armed-close checks that all N closes succeed, then waits through the drain.
Its closed descriptors cannot be read, so this case does not prove that internal callbacks or queued references have been cleaned up.
Kernel lifecycle tests cover that property separately.

Each successful case also emits one `TIMERFD_CASE` JSON record.
It contains `n`, `mode`, `create_ns`, `arm_ns`, `operation_ns`, `foreground_ns`, `close_ns`, `total_wall_ns`, `drain_overshoot_ns`, `expirations`, `nonreadable_after_drain`, `closed`, `expiry_read_p99_ns`, and `expiry_read_max_ns`.
Counts exclude the sentinel.
`closed` counts target descriptors successfully closed during the case; for armed-close these closes are measured by `operation_ns`.
`close_ns` measures final cleanup, including the sentinel.
Expiration latency fields are zero in cases that do not read target expirations.
These four records retain bounded raw timings and validation counts alongside the scalar results.

## Optional churn producer

A server benchmark can start the same helper as a background load:

```sh
/benchmark/bin/timerfd-bench churn 5000 120
```

`RATE` is the requested number of cycles per second and must be a positive multiple of 100.
`MAX_SECONDS` is a positive safety limit.
The producer maintains 128 timerfds and schedules batches every 10ms.
Each cycle arms one timer 50ms into the future, rearms it 60ms into the future, and then either cancels it or closes and replaces it.
Every fourth cycle takes the close path.
Missed batch slots are counted rather than retried, and the requested rate is never reduced.

The producer installs SIGTERM and SIGINT handlers and creates its descriptors before printing and flushing `TIMERFD_CHURN_STARTED`.
Its JSON fields are `pid`, `start_ns`, and `rate`.
The consumer should wait for that record before starting its measured work, then send SIGTERM or SIGINT and wait for the producer.

The final stdout line is `TIMERFD_CHURN_FINISHED` followed by a JSON object with `pid`, `start_ns`, `finish_ns`, `elapsed_ns`, `cycles`, `skipped_slots`, `stop_requested`, and `rate`.
Times use `CLOCK_MONOTONIC`; elapsed time ends when the producer leaves its cycle loop, before closing the remaining descriptors.
The helper exits nonzero if it reaches its time limit without a stop request or if `cycles * 1e9 / elapsed_ns` is below 90% of `RATE`.
It still prints the final record in those cases.
Consumers should require a successful exit, require `stop_requested` to be true, and independently verify the achieved cycle rate.
