# Redis under timer churn

These jobs compare Redis GET throughput and fixed-arrival response times with one or four guest CPUs.
The `none` condition adds no explicit timer load.
The `churn` condition runs `timerfd-bench churn 5000 120` beside Redis until both measurements finish.
Both systems need the benchmark initramfs, KVM networking, and the normal host benchmark dependencies, including Python 3 and `redis-benchmark` on `PATH`.

Run the jobs through the existing benchmark runner:

```sh
bash test/initramfs/src/benchmark/bench_linux_and_aster.sh redis/timer_none_smp1
bash test/initramfs/src/benchmark/bench_linux_and_aster.sh redis/timer_none_smp4
bash test/initramfs/src/benchmark/bench_linux_and_aster.sh redis/timer_churn_smp1
bash test/initramfs/src/benchmark/bench_linux_and_aster.sh redis/timer_churn_smp4
```

The guest starts a fresh Redis instance without persistence.
It seeds `fixed-perf-key` and the default `redis-benchmark` GET key with `abc`.
The host first runs `redis-benchmark -n 100000 -c 20 -t get`.
It then opens 20 persistent connections and schedules 5,000 GET requests at 1,000 arrivals per second for five seconds.
Each connection has at most one outstanding request.
A shared client queue retains scheduled requests when all connections are occupied.
Every fixed-arrival response must equal the nine bytes `$3\r\nabc\r\n`.

Each request records its planned arrival, actual start and finish using the host monotonic clock.
The summaries report these three intervals separately:

| Metric | Interval |
| --- | --- |
| `service` | Actual request start to validated response finish. This includes network and host client work. |
| `client_queue` | Planned arrival to actual request start. This includes host scheduling delay and waiting for a free connection. |
| `total` | Planned arrival to validated response finish. |

Each summary contains `p999_ns` (nearest-rank p99.9), `max_ns`, `error_count`, and `over_10ms_count`.
The latter counts successful intervals strictly greater than 10 ms; it is a descriptive threshold.
Errors are excluded from interval percentiles and counted separately.
All three error counts refer to failed or unstarted planned requests.
Throughput is a separate closed-loop capacity measurement.
Its latency histogram does not supply the fixed-arrival tails.

The runner supplies `GUEST_SERVER_IP_ADDRESS`, `REDIS_PORT`, `BENCHMARK_SYSTEM=asterinas|linux`, and a writable `BENCHMARK_ARTIFACT_DIR` for each system.
For an already running guest, a job's `host.sh` can also be invoked directly with `GUEST_SERVER_IP_ADDRESS` set.
`REDIS_PORT` defaults to 6379.
The guest binds the normal benchmark address `10.0.2.15`; set `REDIS_GUEST_ADDRESS` to bind another configured address.
Without an artifact directory the host creates a unique directory in the system's temporary directory; without a system label it uses `standalone`.

The host prints the unique artifact paths.
`throughput.log` retains the complete combined client output, including partial output on failure.
`result.json` is published by atomic rename and has this version 1 schema:

- `system`, `condition`, `cpus`, `host`, and `port` identify the invocation.
- `status` and `failure` describe the host measurement and control stages.
  `shutdown_verified` records control-connection EOF; guest exit is checked by
  the shared runner.
- `throughput` records `log_path`, `command`, `requests`, `concurrency`,
  `exit_code`, `host_elapsed_ns`, and, on successful parsing, `rps`.
- `fixed` records `offered_rps`, `duration_s`, `connections`, `planned`,
  `started`, `completed`, `error_count`, `failure`, `summaries`, and `records`.
  `completed` counts requests whose attempt finished, including failed attempts.
  `records` always contains all 5,000 IDs.
  Each record has `id`, `scheduled_ns`,
  `started_ns`, `finished_ns`, `connection_id`, `local_port`, `error`, and
  `response_bytes`.
  Unstarted fields are null and their error is `not started`.
- `load` retains `start_raw`, `log`, and `exit_code` from the guest.
  For churn it
  also records parsed `start` and `finish` helper records and `achieved_rate`.
  Fields for stages not reached can be absent.
- `control` retains each Redis control command with its host timestamps and
  reply or error.
  Control commands run outside the measured interval.

Redis keys and blocking lists coordinate readiness, completion and teardown.
The guest publishes readiness only after the churn start record and a live helper process are observed.
After both workloads, the host sends `done`; the guest checks that the helper remains alive, sends TERM, waits for its successful exit and publishes its final log.
The host requires the same PID, a target rate of 5,000, positive consistent timestamps and cycles, `stop_requested=true`, and an achieved rate of at least 4,500 cycles per second.
This handshake establishes coverage without comparing host and guest clock origins.

The host sends `ack` even after a measurement or validation failure.
The guest then shuts down its own Redis and waits for its exit.
The host waits for control connection EOF before emitting scalar result lines; `shutdown_verified` records only that EOF observation.
The host's JSON and scalar output do not prove that the Redis process exited successfully.
The guest prints `REDIS_TIMER_SERVER_EXIT <status>` and returns failure when its owned Redis wait fails.
Each result YAML sets `runtime_config.check_guest_exit: true` so the shared runner checks the guest's exit status before publishing final benchmark results.
Standalone runs must also check that exit status and the server exit marker in guest output.
Redis cannot publish its process exit status after shutdown.
Guest output retains the server and churn logs.
Startup, client, control, helper-stop and shutdown waits are bounded.
Failed runs exit nonzero and retain partial host artifacts; SIGKILL or loss of the host filesystem can prevent final JSON publication.
