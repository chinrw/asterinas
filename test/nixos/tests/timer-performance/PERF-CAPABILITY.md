<!-- SPDX-License-Identifier: MPL-2.0 -->
# Guest instruction-sampling capability

Run the manual `test_nixos_full.yml` workflow with `mode=perf-capability` in `chinrw/asterinas` to check whether this runner can capture a KVM guest's instruction pointer. This host-tool check uses a one-vCPU, 64 MiB fixture and does not build or modify Asterinas.

The owned boot disk is protected by a disposable QEMU snapshot overlay. The boot sector normalizes CS, writes a readiness marker through port 0xe9, disables interrupts and loops at a known address. The probe requires that marker before recording. It separately records software `cpu-clock` and, if hardware counting is available, `cycles`. An event establishes capability only if the binary perf data contains at least three guest-context samples at the exact loop RIP and the owned QEMU remains alive. Host-only samples and successful command exits are insufficient.

Artifacts preserve the boot bytes, expected RIP, tool/kernel identities, event commands, raw perf files and dumps, loss counters, stdout/stderr and `result.json`. Each event has an independent result. Losses are reported; later profiling must assess its own coverage. A negative result does not authorize interpretation of absent guest samples as an idle guest.

The workflow installs Ubuntu host diagnostic packages and records the canonical perf binary it selects. It does not change perf permissions, kernel tracing settings or sysctls. It attaches only to the owned QEMU process and bounds recording and cleanup. The ephemeral runner bounds any uncatchable cancellation.

Local syntax, fixture and cleanup checks can be run without performance-counter access:

```sh
python3 test/nixos/tests/timer-performance/perf_capability.py --self-test --out /tmp/perf-capability-check
```

On an authorized host with KVM and counter access, set `TAIL_PERF_BIN` to the actual perf executable and omit `--self-test`. A nonzero exit and the saved error mean guest-IP capability was not established.
