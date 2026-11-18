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

### Conformance Test Suite - gVisor Exception

While most tests rely on `Nix` for compilation, the `gvisor` conformance test suite currently cannot be built with `Nix`. Instead, the `gvisor` tests are compiled in the Docker image. For details, refer to `tools/dev_env/docker/kernel-dev/Dockerfile`.

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
repository's pinned Nix dependencies, then boots a fresh QEMU guest for each
registered workload. A workload is a test program or a compound shell test,
such as a server/client pair. It does not build the Asterinas kernel.
This check validates the tests on a reference platform; Asterinas runtime tests
are still needed to validate the same behavior on that kernel.

The x86-64 reference profile uses four-level paging (`la57=off`). It does not
validate 57-bit virtual addresses. Permission-sensitive mmap and exec tests
explicitly remove `CAP_SYS_RAWIO`, including the exec bounding set when needed.
The guest sets the mmap floor to 64 KiB so page one exercises the same denial as on Asterinas.
File-backed RSS tests use `TEST_TMPDIR=/ext2`. These workload preconditions are
shared by the Linux and Asterinas test entries, rather than weakening their
assertions to accommodate the guest's initial privileges or root filesystem.

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

Each group registers its workloads through `run_test_case` in the existing
`run_test.sh`. The bundle collects this list without executing tests, and the
host selects each workload in a fresh VM with fresh disk snapshots. A failing
workload does not prevent later workloads in the same group from running.
The ordinary Asterinas suite retains its existing stop-on-error behavior.

`results.tsv` gives each selected workload a PASS, FAIL, SKIP, or NOT_RUN result,
its command, exit status, and an explanation. A workload passes only when it
starts, reports a successful completion, and QEMU exits successfully. Setup
failures are NOT_RUN, and a timeout after the workload starts is FAIL. Cancelling
the runner records the workloads whose completion was not collected. Any FAIL
or NOT_RUN makes the host return nonzero.

This accounts for registered workloads, not every function or branch inside
them. A fatal `CHECK` still terminates that program, so later constructor tests
inside it have not run even though the host continues with the next workload.

The benchmark kernel ships without loadable modules. Workloads that require a
missing built-in feature are recorded as SKIP with its configuration symbol.
The runner reports excluded TDX, graphics, virtual-console, input-device, and
Asterinas-specific TCP tests. Missing fixtures required by the remaining tests
are errors. When adding tests, register them with `run_test_case` in the existing group entry;
do not maintain a separate Linux copy of the complete test list.

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

The workflow builds one bundle and distributes runnable workloads in manifest
order across four GitHub Actions shards. Set the repository variable
`LINUX_REGRESSION_SHARDS` or the manual workflow input `shards` to change the
count. A manual run can also select a group or workload and include temporary
exclusions. Capability and hardware exclusions remain visible in the report.

Open **Linux regression report → Print regression report** in Actions for every
workload's full console log, followed by the English results table and test
summary. A failed workload does not stop other workloads or shards. A missing
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
