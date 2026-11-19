# Initramfs-Based Test Suites

This directory contains the test suites of Asterinas running in initramfs, including in-house syscall regression tests, third-party Linux conformance suites, benchmarks, configuration files, and Nix expressions to package them.

## Directory Structure

```
initramfs/
├── etc/               # Configuration files packaged into initramfs
├── nix/
│   ├── benchmark/     # Nix expressions for `benchmark`
│   ├── conformance/   # Nix expressions for `conformance`
│   ├── regression/    # Nix expressions for `regression`
│   └── initramfs.nix  # Nix expression for packaging initramfs
├── src/
│   ├── benchmark/     # Third-party benchmark suites to compare Asterinas against Linux
│   ├── conformance/   # Third-party test suites to verify Linux compatibility
│   │   ├── gvisor/    # Gvisor syscall test suite
│   │   ├── kselftest/ # Linux kernel in-tree selftests
│   │   └── ltp/       # Linux Test Project syscall test suite
│   ├── regression/    # In-house syscall and subsystem regression tests
│   └── boot_hello.sh  # Minimal boot smoke test script
├── Makefile
└── README.md
```

## Building and Packaging Tests

Most tests in this directory are compiled and packaged using [Nix](https://nixos.org/), a powerful package manager. This ensures consistency and reproducibility across environments.

> **Note**: If you are adding a new test to the `regression` directory, ensure that it supports multiple architectures. Some of the existing tests lack proper architecture-specific handling.

### Linux Kernel Selftest (kselftest)

The `kselftest` suite builds a subset of Linux's in-tree selftests
(`tools/testing/selftests`) from the Linux version pinned in
`nix/conformance/kselftest.nix` (currently **v6.18**). Only the
subsystems listed in `baseKselftestTargets` are compiled, plus `x86`
on x86_64 hosts.

Invoke via:

```bash
make run_kernel AUTO_TEST=conformance CONFORMANCE_TEST_SUITE=kselftest
```

**Bumping the Linux version.** Edit `version` in
`nix/conformance/kselftest.nix`, temporarily set `hash = lib.fakeHash;`,
run the Nix build once, and copy the real hash reported by `fetchgit`
back into `hash`.

**Blocklist layout.** `src/conformance/kselftest/blocklists` is the
default blocklist; `blocklists.ext2` and `blocklists.exfat` hold
filesystem-specific extras. See the header of `blocklists` for the
entry format.

### Multi-Architecture Support

The test suite supports building for multiple architectures, including `x86_64` and `riscv64`. You can specify the desired architecture by running:

```bash
make kernel TARGET_ARCH=x86_64
# or
make kernel TARGET_ARCH=riscv64
```

The build artifacts (initramfs) can be found in the `test/initramfs/build` directory after the compilation.

## Running Regression Tests on Linux

The Linux regression runner checks the in-house tests against a fixed x86-64
Linux 6.16.0 kernel image shared with the benchmarks. It builds the tests and initramfs with the
repository's pinned Nix dependencies, then boots one QEMU guest per subsystem
with runnable workloads. A workload is a test program or a compound shell test,
such as a server/client pair. It does not build the Asterinas kernel.
This check validates the tests on a reference platform; Asterinas runtime tests
are still needed to validate the same behavior on that kernel.

The x86-64 guest uses four-level paging and a 64 KiB minimum mmap address.
Its Q35 VGA, NVMe, and block-device PCI slots match the device-model tests.

On an x86-64 Linux host with Nix and access to `/dev/kvm`, run from the repository
root:

```bash
bash tools/run_linux_regression.sh
```

The first run downloads or builds the dependencies. The bundle includes QEMU
and the disk formatting tools. Use `--accel tcg` to explicitly request software
emulation on hosts without KVM.

Logs, kernel configuration, input hashes, `cases.tsv`, and `results.tsv` are written to
`test/initramfs/build/linux-regression`. The output directory must be empty;
choose a new directory to preserve earlier results. To run one group:

```bash
bash tools/run_linux_regression.sh --group time --output /tmp/linux-regression-time
```

To reproduce one workload, use its group and number from `cases.tsv`:

```bash
bash tools/run_linux_regression.sh --group time --case 1 --output /tmp/linux-regression-time-1
```

The numbers belong to the built bundle; use its manifest when replaying a result.
Use `--bundle PATH` to reuse a bundle previously built with
`nix-build test/initramfs/nix/linux-regression.nix`. Each VM receives temporary
snapshots of dedicated ext2, exFAT, and NVMe images. Tests do not access host
block devices.

The Linux bundle derives its manifest from the group entry scripts. A script
that lists literal executable paths can be used unchanged. The Linux build
wraps each path in the copy installed in the guest. Groups with arguments,
architecture checks or compound commands explicitly register their workloads
with `run_test_case`. Unsupported syntax in a simple entry fails the build.

Within each subsystem the guest runs the selected workloads in order, each
in a separate process and PTY. Mounted filesystems and other guest kernel state
persist across workloads. A failed or timed-out workload lets the next one
run. If the guest exits early, completed results are retained, an interrupted
workload fails and all unstarted workloads are marked NOT_RUN. Other subsystems
continue. Use `--mode case` to start a clean VM per workload when investigating
an order-dependent result or using an older bundle.

`results.tsv` gives each selected workload a PASS, FAIL, SKIP, or NOT_RUN result,
its command, duration, exit status, and an explanation. Subsystem artifacts
include the full guest console and result channel, per-case logs and
`guest-times.tsv` with the measured wall time of each boot and its workloads.

This accounts for registered workloads, not every function or branch inside
them. A fatal `CHECK` still terminates that program, so later constructor tests
inside it have not run even though the host continues with the next workload.

The benchmark publishes its 6.16.0 kernel image and configuration. The bundle
builds OverlayFS, exFAT, and AF_VSOCK as external modules using the Linux 6.16
sources, the headers embedded in that image, and its exported symbols.
Each workload loads its required module before its start marker. The exFAT
workloads also mount the test disk. A missing driver or failed mount produces
NOT_RUN. The VirtIO hardware RNG workload remains skipped.

The runner reports excluded TDX, graphics, virtual-console, input-device, and
Asterinas-specific TCP tests. Missing fixtures required by the remaining tests
are errors. Add new workloads to the existing group entry so both platforms
continue to use the same test list.

Temporary Linux exclusions are maintained in
[`linux-regression-exclusions.tsv`](linux-regression-exclusions.tsv).
Each row names an exact group and command, its category, the reason for the
exclusion, and the condition for restoring it. Copy the group and command from
`registered-cases.tsv`; workload numbers are not exclusion keys. Unknown,
duplicate, and ambiguous entries are errors, so adding a test before an existing
workload cannot silently redirect its exclusion to another program.

Temporary exclusions produce SKIP results with their reasons. They exclude the
whole workload, including functions inside that program which might otherwise
pass. Use `--include-excluded` to execute these workloads when checking a fix:

```bash
bash tools/run_linux_regression.sh --group fs --case 2 --include-excluded --output /tmp/linux-regression-fs-2
```

This option does not enable the separate hardware and TDX profile exclusions.
Use `--exclusions PATH` to validate an alternative temporary-exclusion table.
Results retain the original registered manifest, the effective selected manifest,
the exclusion table, and its hash.

The workflow builds one bundle and assigns whole subsystems to four GitHub
Actions shards, balancing the number of runnable workloads. Case mode uses
round-robin workload assignment. Set `LINUX_REGRESSION_SHARDS` or the manual
`shards` input to change the count. Manual runs also support `mode` (subsystem
or case), group and workload selectors, and temporary-exclusion validation.

Open **Linux regression report → Test summary** in Actions for the English results
table and totals. Expand **Full workload logs** in the same job to inspect any
workload's console output. A failed workload does not stop other workloads or shards. A missing
shard, result, or completed workload log fails the combined check. Each shard
also retains its original console files and results as an artifact.

After updating the kernel, tests, or fixtures, explicitly rerun the affected
temporary exclusions and remove entries whose restoration conditions are met.
Remove workflow-level path filters before making this check required for every
pull request.

## Supported Benchmarks

The following benchmarks are currently supported:

- fio
- hackbench
- iperf3
- lmbench
- memcached
- nginx
- redis
- schbench
- sqlite
- sysbench

### Architecture Compatibility

All benchmarks except `sysbench` support both `x86_64` and `riscv64` architectures.

These benchmarks are precompiled and packaged into the Docker image for convenience. Refer to `tools/dev_env/docker/prebuilt-nix-packages/Dockerfile` for details.

## Adding New Benchmarks

We recommend utilizing `Nix` when adding new benchmarks. To check if a benchmark is already available, use the [`Nix Package Search`](https://search.nixos.org/packages?channel=26.05). If a package exists in the Nix channel, you can directly use it or modify it if necessary.

If the desired benchmark is not available or cannot be easily adapted, you can add a custom `.nix` file to package it manually. Place the `.nix` files under the `test/initramfs/nix/benchmark` directory.

## Configuration Files

Configuration files required by benchmarks or regression tests should be placed in the `test/initramfs/etc` directory.

If additional configuration files or directories are needed, ensure they are appropriately packaged by updating the `initramfs.nix` file.

## Notes for Developers

- **Nix Usage**: Use `Nix` whenever possible to manage dependencies and builds for ease of maintenance and consistency.
- **Multi-Architecture Support**: Ensure new regression tests or benchmarks properly support multiple CPU architectures.
