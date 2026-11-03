<!-- SPDX-License-Identifier: MPL-2.0 -->
# Guest instruction sampling

This fork diagnostic records the owned QEMU vCPU threads with perf's cpu-clock event at 99 Hz. QMP supplies the thread list, and each thread is checked against the owned process and its start ticks. Explicit TID targeting and disabled inheritance exclude transient I/O workers, whose exit previously left the process-wide recorder repeatedly polling a hung-up event descriptor. Host execution on the selected vCPU threads remains visible.

The existing eight-guest plan retains one unprofiled, batch-disabled pair and three profiled, batch-enabled pairs with four vCPUs and the original workload sequence. A bounded host-side readiness barrier precedes the micro workloads. It confirms target selection and recorder launch; actual sample and window coverage are checked separately. Sampling adds overhead, so these results remain separate from unprofiled performance measurements.

Before either kernel build, a disposable four-vCPU KVM fixture uses the same Nix perf executable, recorder, and validator for 35 seconds. It requires the expected guest instruction address, monotonic timestamps, disabled inheritance, samples confined to the selected TIDs, complete finalization, no reported loss, and owned-process cleanup. More than 100,000 returned polling iterations rejects gross observer spin. Passing this guard does not establish performance neutrality.

The supervisor stops the recorder before QEMU cleanup and saves its CPU usage from wait4. Perf normally re-raises an intentional SIGINT after finalizing its file. That exit is accepted only with the exact requested signal, coherent lifetime, finalization marker and sample count, complete raw data, and the existing clock, identity, coverage and loss checks. Original exit status and validation are preserved. Early exits and TERM/KILL escalation remain failures.

The six fixed-rate request windows are reconstructed from host CLOCK_MONOTONIC timestamps and linked to the exact saved kernel ELF. Elapsed-only phase records do not establish individual absolute microbenchmark, warmup or Go windows. Perf CPU numbers identify host CPUs; QMP indices and OSTD logical CPU IDs are also separate namespaces. Sparse instruction samples and inline symbol chains do not establish call stacks or exclude unobserved paths.

The workflow records the allocated EPYC model. Measurements on a different model must remain a separate hardware cohort and cannot establish a performance delta against earlier 7763 runs.
