# Timer retirement measurements

These are raw measurements for the two commits identified in metadata.json.
The benchmark code is part of the benchmark-only commit and identical in the fix commit.
This branch contains data, rather than a build image or a reproduction bundle.

timerfd.csv and redis.csv contain all 32 per-workload observations each.
timerfd-cases.jsonl contains all 128 raw helper case records.
requests/ contains every one of the 160000 fixed-arrival GET records in 32 files.
results/ contains the host result/control/load metadata, with request arrays split into the linked JSONL files.
throughput/ contains the full native redis-benchmark output for every condition.
No formal guest failed, no sample was removed, and no rounds were added.
The numeric timestamps, counts and errors are unchanged; machine-local provenance paths were removed.
metadata.json records the versions, image/helper identities, environment and plan/order audits.
The failed preflights are described there and remain retained in the local research record.

Each CPU setting has four base/head pairs, two in each execution order.
The workload order inside every fresh guest is Redis none, Redis churn, timerfd64 and timerfd4096.
The Redis instances use ports 6379 and 6380 respectively.
Published measurements use the same NixOS userland in both variants; the normal initramfs runner is also validated on Asterinas and Linux.
Absolute numbers depend on the userland and virtual hardware configuration.

tables.md has the two principal tables.
Cells show base to head; each variant has four observations per cell.
Operation numbers are medians of per-run stage averages in ns/op.
Quantile numbers are medians of per-run nearest-rank quantiles, with the complete range in brackets.
They are not percentiles pooled across all requests.
Maximums are the worst observed per-run maximum.
Paired H/B is calculated per CPU/block and then its median is reported.
It need not equal the ratio of the two independent medians.

Redis service includes network and host response processing; client queue includes host scheduling and waiting for a free connection; total spans planned arrival to completed response.
Counts above 10ms are summed over 20000 requests per variant/cell and use a strictly-greater threshold.
All error counts are zero.
Timerfd expiry is deadline-to-completed-read, including delivery, scheduling, poll/scanning and reads.
With 64 timers nearest-rank p99 is the per-run maximum.
Four pairs are descriptive evidence and do not establish a universal speedup or an absence of regressions.
