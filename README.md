# Timer lifecycle measurements on current main

These measurements compare main acf7dcf5e with timer fix f48cf611f.
[Readable result tables](tables.md) separate operation costs, expiry lateness,
Redis throughput and request tail latency.
There are sixteen local Ryzen 9 9950X QEMU/KVM guests: four base/head pairs
for each of one and four CPUs, with two pairs in each order. Every guest uses
8GiB, TAP networking and VHOST=off. Both normal kernels use release-lto,
identical dependency locks, the same NixOS userland and the same helper binaries.
Metadata records their hashes and the planned and observed order.

The CSV files retain every formal observation. The requests directory contains
all 160000 fixed-arrival GET records, split into thirty-two files. The results
directory links those records to their original client summaries. Capacity logs,
128 timerfd case records and per-pair order comparisons are also retained.
No formal trial failed or was replaced. The initial wrong-boot-wrapper preflight
was interrupted and retained locally; the corrected preflight passed before
this formal plan was frozen.

Operation values are stage-average ns/op. Expiry spans deadline to completed
read, including delivery, scheduling, polling and reads. Quantile summaries use
the median of four per-run nearest-rank quantiles, not a pooled percentile.
Maxima are worst observed; counts above 10ms use a strict greater-than comparison.
Four pairs are descriptive evidence. Throughput does not replace tail latency.

## Run the reviewer workloads

Use the project development container with KVM, the normal benchmark host
network setup, Python 3 and redis-benchmark. Fetch the three commits below, then use separate clean checkouts of
acf7dcf5e and f48cf611f. Apply the same benchmark and dependency patches in each:

```sh
git fetch https://github.com/chinrw/asterinas.git c0db432e39b50cd0f85a83c549ab65223cbe538b 461280bdbe7cf77d0bcee36940c6938a56403897 f48cf611fa6a78e27c374759957276555f6416e1
git show --format= c0db432e39b50cd0f85a83c549ab65223cbe538b | git apply
git show --format= 461280bdbe7cf77d0bcee36940c6938a56403897 | git apply
for cpus in 1 4; do
    for count in 64 4096; do
        bash test/initramfs/src/benchmark/bench_linux_and_aster.sh timerfd/count${count}_smp${cpus}
    done
    for background in none churn; do
        bash test/initramfs/src/benchmark/bench_linux_and_aster.sh redis/timer_${background}_smp${cpus}
    done
done
```

The first patch supplies maintained benchmark jobs and packaging. The second
pins unwinding to 0.2.10: an unmodified main control fails compiling 0.2.11
with the pinned nightly. This support patch stays outside the timer PR.
Keep dependency resolution, helper binaries, hardware and kernel configuration
identical in both checkouts; kernel.lock records the author's generated lock.
The suite READMEs define the metrics, sentinel/drain/count checks and artifacts.

The maintained runner boots initramfs guests and a Linux control per job;
the published tables batch the same workloads within fresh NixOS guests.
Userland and history can change absolute results. Balance base-first and
head-first pairs before collecting data and retain slow and failed trials.
