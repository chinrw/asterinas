# Run the fork-only timer tests

The proposed upstream patch is `ec33c743471209bbb302f9e116b9e307703c77a3` on main `a9c942d275631c14f637745e63599852c90c2303`.
It changes only `kernel/core/src/time/core/timer.rs`, including the kernel regression tests.
The timerfd/Redis benchmarks stay in fork commit `c0db432e39b50cd0f85a83c549ab65223cbe538b`.
The standalone Go program stays in the older fork harness commit `1185091b9d739fc418ffc37e2b2b4a549200dd55`.

## Timerfd and Redis

Use disposable checkouts of main and the proposed fix.
Run these commands in each checkout to add the same benchmark harness without changing the checked-out kernel:

```sh
timer_benchmark_commit=c0db432e39b50cd0f85a83c549ab65223cbe538b
git fetch https://github.com/chinrw/asterinas.git "$timer_benchmark_commit"
git show --format= --no-color --binary "$timer_benchmark_commit" | git apply
```

The patch adds benchmark sources, Nix packaging and runner support.
It leaves the timer implementation in each checkout intact.
Applying it to the proposed base reproduces the full measured source tree at `c0db432e3`; applying it to the proposed fix reproduces the full measured source tree at `5b7726ff9`.
Both equalities were checked with Git tree hashes and are recorded in `reviewer-scope.json`.

Inside the project development container, with KVM and TAP permissions:

```sh
make install_osdk
make -C test/initramfs install_host_pkgs
for smp in 1 4; do
    for n in 64 4096; do
        VHOST=off bash test/initramfs/src/benchmark/bench_linux_and_aster.sh timerfd/count${n}_smp${smp} x86-64
    done
    for condition in none churn; do
        VHOST=off bash test/initramfs/src/benchmark/bench_linux_and_aster.sh redis/timer_${condition}_smp${smp} x86-64
    done
done
```

Each job boots an Asterinas guest and a Linux control.
Logs and Redis request records remain under `target/benchmark-artifacts/`.
The suite READMEs define the metrics and checks.
Use identical helpers, userland, kernel build settings, hardware and memory on both sides, with fresh guests and balanced base/head order.
Keep failures and slow runs in the comparison.

The published timerfd/Redis tables used 16 local Ryzen 9 9950X QEMU/KVM guests, four base/head pairs per CPU setting with two pairs in each order.
All 160000 fixed-arrival GETs succeeded.
Those measurements batch Redis none/churn and timerfd64/4096 inside NixOS guests; the commands above use fresh initramfs guests per job, so absolute values can differ.
The code and workload are the same.
The recorded 34-job CI run tested `5b7726ff9`, which has the same kernel code as the proposed fix plus this benchmark harness.
It is not a new CI run on the fix-only commit.

## Standalone Go reproducer

The cgo startup barrier is already a separate Go source file, with its Go license and Nix build recipe.
Download the three files from the immutable fork commit:

```sh
mkdir -p reviewer-go
timer_go_source=https://raw.githubusercontent.com/chinrw/asterinas/1185091b9d739fc418ffc37e2b2b4a549200dd55/test/nixos/tests/development-tools-go
for file in minimal_cgo.go minimal_helper.nix GO-LICENSE; do
    curl --fail --location "$timer_go_source/$file" --output "reviewer-go/$file"
done
```

The build recipe uses Go 1.26, GCC and `GOEXPERIMENT=goroutineleakprofile`.
Run the resulting executable without arguments and use the same binary on both kernels.
The optional `yield` argument changes the workload and was not used for the original-helper results.
The short wait belongs to the Go runtime; the extracted source does not call `time.Sleep`.

Historical measurements used one-vCPU, eight-GiB fresh guests on EPYC 7763 in fork Actions.
The host required both `BARRIER_OK` and successful command completion within three seconds.
This endpoint includes command/output transport and exit observation, not just Go execution time.
The reviewed harness and its process ownership/deadline checks are in the same fork commit.

| Historical comparison | Base completed by 3s | Fixed completed by 3s | Fork run |
| --- | ---: | ---: | --- |
| `b6c7d55ce` to `567d337dc` | 10/25 | 25/25 | [40648624606](https://github.com/chinrw/asterinas/actions/runs/40648624606) |
| `449721d9` to `cba4d13d` | 9/25 | 25/25 | [40657435112](https://github.com/chinrw/asterinas/actions/runs/40657435112) |

The two campaigns remain separate; both retain their deadline failures and exclude longer-wait controls from these denominators.
Successful-run medians were about 0.4 seconds on both sides.
The observed benefit was completing within the deadline more consistently.
These are older kernel comparisons: the current main/proposed-fix pair has not rerun this Go comparison, and the current timerfd/Redis campaign omitted Go.

The raw historical records, paired plans and summaries are in go-history/.
The records retain all 54 attempts per campaign, including the separate longer-wait controls.
A driver_exit of -9 records owned supervisor cleanup; it is separate from the helper outcome.
