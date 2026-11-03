<!-- SPDX-License-Identifier: MPL-2.0 -->
# Guest instruction sampling

This fork-only diagnostic records the owned QEMU process with perf's cpu-clock event at 99 Hz. It preserves host monotonic timestamps, guest instruction pointers, host CPU numbers, and QEMU thread IDs. It neither pauses the VM nor changes the production timer implementation. Sampling adds overhead, so this campaign remains separate from unprofiled performance measurements.

The existing eight-guest plan retains one unprofiled, batch-disabled pair and three profiled, batch-enabled pairs, all with four vCPUs and the original workload sequence. Before either kernel build, a disposable one-vCPU boot fixture must produce actual guest instruction samples using the same Nix perf executable. The hardware cycles event is not used for the workload campaign.

The supervisor attaches once after verifying the atomic QEMU owner record and stops the profiler before cleaning up QEMU. A profiler error cannot bypass the existing VM cleanup. The raw data, command, process identity, exit status, and exact kernel ELF remain in the artifacts.

Coverage validation reconstructs the six fixed-rate request windows from their host CLOCK_MONOTONIC timestamps. The recorder also spans earlier workload history, but elapsed-only phase records do not establish individual absolute microbenchmark, warmup, or Go windows. A successful record requires monotonic perf attributes, actual guest samples, no reported loss, the owned QEMU PID, coverage of every fixed-rate window, and a guest kernel sample within the exact ELF's executable ranges.

Perf CPU numbers identify host CPUs. The saved QMP CPU-to-thread map is needed to identify guest vCPUs. Sampling is statistical: a short or unsampled path may still contribute to a tail, and a sampled instruction is not a stack trace. Failure of any evidence gate remains a failed sample.
