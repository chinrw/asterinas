# Temporary timer batch probe

This diagnostic records elapsed timer-dispatch stages on SMP kernels without changing callback selection, execution, or drop ordering. It is for an isolated x86-64 guest. It does not establish the cause of previously observed tails. Kernel compilation and guest validation remain the parent's integration work.

`apply_batch_probe.py CHECKOUT` accepts only exact timer source SHA256s for base `e0d8b839f95f608c8a7e9c8942e01614a3b3b260` and fix `64848d3e0a23ff0903f4be485fb7750c1709a575`. First verify the complete plain variant identity. The script validates every injection anchor before writing, copies `batch_probe.rs`, adds its private module, adds the custom prctl branch, and injects equivalent hooks into the heap or BTreeMap timer implementation. It makes no commit. Reapplication fails; restore plain sources first.

Compile `batch_probe.c` with the original guest Nix gcc package, for example `-std=gnu11 -O2 -Wall -Wextra`. Run it as root after networking is ready. It checks the ABI, binds UDP `10.0.2.15:9998`, prints and flushes one `TIMER_BATCH_READY version=1 threshold_ns=<effective>` line, then stays in the same process. The original timer-perf and minimal Go binaries remain unchanged. The UDP interface is unauthenticated and belongs only on the isolated experiment network.

The optional helper argument `--threshold-ns N` sets a private threshold override. Zero or omission means 5,000,000 ns; 1 through 1,000,000,000 selects that threshold. Use a low override only for explicit event-path calibration, and preserve it in metadata. Production diagnostic cohorts must validate header word 16 equals 5,000,000. No wire layout changes with the override.

## UDP protocol

Every field is an unsigned 64-bit little-endian integer. Requests contain exactly five words (40 bytes):

| Word | Meaning |
| --- | --- |
| 0 | Magic `0x4153544241544348`. |
| 1 | Version 1. |
| 2 | Command: PING=0, CLOCK=1, START=2, FREEZE=3. |
| 3 | Caller nonce, echoed unchanged. |
| 4 | Case ID: zero for PING/CLOCK, nonzero for START, matching current case for FREEZE. |

Replies contain exactly twelve words (96 bytes): magic, version, echoed command, nonce, case ID, positive Linux errno (zero on success), helper TSC before processing, helper TSC after processing, frequency Hz, last observed epoch, successful dump bytes, and total EBUSY retries across FREEZE and READ.

PING and CLOCK both call kernel INFO. Use CLOCK round trips to bracket guest TSC against host timestamps; host and guest clocks are not assumed equal. START enables recording before replying. FREEZE disables admission, retries EBUSY on FREEZE and idempotent READ at 1 ms intervals within one shared two-second monotonic deadline, and writes `/var/tmp/timer-batch-<caseid>.bin` through a temporary file and rename before replying. Its TSC interval includes draining and file I/O and is unsuitable for low-latency calibration. A timeout leaves admission disabled and old-epoch writes preserved; another explicit FREEZE can retry after the host records the failure. Non-EBUSY control errors are reported immediately.

Malformed lengths produce EINVAL and a stderr report. Invalid magic/version produces EPROTO; unknown commands produce EINVAL. Partial fields in malformed packets are zero-padded for error replies. Validate reply size, magic, version, nonce, command, case, and errno before interpreting other fields. ABI failure is fatal at startup. The helper does not hide lost UDP packets or retry START. START is not idempotent: retrying after a lost successful reply yields EBUSY. Preserve that ambiguity. Choose unique case IDs; a later successful dump of the same case replaces its file.

Successful FREEZE reports 20,736 dump bytes. Export files via the existing base64 path after all workloads. Keep the daemon alive across cases: ownership belongs to its PID, and restarting it requires a fresh guest for this experiment.

## Kernel control ABI

Use `prctl(0x41534250, command, arg3, arg4, 0)`. Every command requires effective root. The first START establishes the owner PID. Other root PIDs may INFO but cannot START, FREEZE, or READ.

| Command | arg3 | arg4 | Return |
| --- | --- | --- | --- |
| INFO=0 | Eight-word buffer. | At least 64 bytes. | 64 bytes. |
| START=1 | Nonzero case ID. | Threshold override ns, 0 for default. | Zero. |
| FREEZE=2 | Matching case ID. | Zero. | Snapshot size, or EBUSY while admitted batches remain. |
| READ=3 | Snapshot buffer. | At least 20,736 bytes. | 20,736 bytes, only after successful FREEZE. |

INFO returns magic, version, header words (32), event words (20), capacity (128), frequency Hz, epoch, and instantaneous admission count. START requires frozen admission and zero active count; after the initial epoch it also requires a prior successful quiescence acknowledgment. It increments epoch and resets aggregates and event commits. Epoch overflow returns EOVERFLOW. Threshold cycles are precomputed at START using ceiling division; hooks read the cached threshold rather than querying frequency. The helper rejects an uninitialized frequency at startup.

The control syscall lock serializes ownership, epoch transitions, and copying atomic records into a preallocated Vec. Allocation occurs before locking; `VmIo::write_slice` copies the private Vec to userspace after unlocking. Timer hooks do not acquire this lock, allocate, print, or disable preemption. Disabled admission does not touch or initialize the monotonic clock singleton.

## Binary snapshot

The file is 2,592 native x86-64 little-endian u64 words: a 32-word header followed by 128 events of 20 words. Unused slots are zero. Ignore slots beyond the stored count. Check each stored commit equals slot index plus one and its epoch matches the header.

| Header words | Meaning |
| --- | --- |
| 0–5 | Magic, version, header words, event words, capacity, frequency Hz. |
| 6–11 | Epoch, case ID, owner PID, START TSC, admission-disabled TSC, successful-quiescence TSC. |
| 12–17 | Qualifying event attempts, stored events, overflow, active count captured at READ readiness, effective threshold ns, reserved zero. |
| 18–24 | Completed batches, maximum queue length, sum elapsed ticks, maximum elapsed ticks, maximum acquisition wait ticks, maximum collection ticks, maximum callback-stage ticks. |
| 25–31 | Cancelled queue removals, expired selections, failed owner upgrades at call, invoked callbacks, maximum collection-time lateness of invoked callbacks ns, bad-TSC batches, batches with differing entry/exit CPU IDs. |

| Event word | Meaning |
| --- | --- |
| 0–3 | Release commit (slot+1), epoch, manager address ID, racy entry CPU ID. |
| 4–9 | Start TSC, before queue acquisition, after acquisition, collection done under the queue lock, callback stage start after unlock, end after the callback loop. |
| 10–11 | Queue lengths after acquisition and after collection. |
| 12–16 | Cancelled queue removals, expired selections, failed owner upgrades at call, invoked callbacks, maximum collection-time lateness of invoked callbacks ns. |
| 17–19 | Clock kind (1 identifies the singleton MonotonicClock allocation, 0 means other), racy exit CPU ID, flags. |

Flag bit 0 marks nonmonotonic TSC stages, bit 1 marks the skipped callback stage on an empty queue, and bit 2 marks differing entry/exit CPU IDs. Empty-queue collection still records an under-lock timestamp; its callback start equals batch end, preserving acquisition delays even if another CPU emptied the queue first.

No owner cache is inspected during collection: there is no added `Weak::strong_count()` read or Weak upgrade. The existing private callback method reports its original outcome: 0 for failed owner upgrade, 1 for cancellation after timer locking, and 2 after invoking the registered callback. Its upgrade, lock, cancellation check, callback, and local drops retain their original order. Other existing call sites ignore the result. The probe counts outcomes only after that call returns. Cancellation after selection is derived as `expired selections - failed upgrades - invoked callbacks`. Cancelled removals are separate and may include entries whose deadlines have not been reached.

The original collection-time clock snapshot remains available after queue unlock. After an invoked callback, its immutable old expiry is subtracted from that snapshot for lateness, saturated to u64 ns. No additional clock read is introduced. This measures deadline lateness at collection among callbacks subsequently invoked; it is not callback-entry lateness. Other clocks remain explicitly marked other and must not be interpreted as absolute monotonic timestamps.

The callback stage includes the existing calls, locks, cancellation checks, Arc drops, and post-call diagnostic accounting. Queue guard release remains at the original block boundary. Collection ends before unlock and callback time starts after unlock, so that interstage gap contributes only to total elapsed.

A batch claims a slot only if valid total elapsed reaches the effective threshold or invoked-callback collection-time lateness reaches it. Short batches still update aggregates. Bad-TSC batches increment their counter and do not contribute duration aggregates; they can still emit an event for valid deadline lateness. Overflow discards subsequent records without overwriting earlier events; aggregates continue. All timer managers are observed while enabled, not only Redis timers.

## Concurrency and interpretation

Admission reads the enabled epoch, increments ACTIVE, and rechecks the same epoch, all SeqCst. Rejected admission decrements ACTIVE without event or aggregate writes. FREEZE disables admission and reports EBUSY until accepted batches finish. Stack-local Batch state works across CPUs and nested scopes; there is no shared mutable TimerScope. Each batch publishes a write-once event with a release commit and finishes aggregate updates before decrementing ACTIVE, including on empty returns.

START never resets ACTIVE and never resets before successful freeze. A thread delayed before incrementing ACTIVE can transiently increment after freeze, but its epoch recheck rejects it, so it cannot corrupt the frozen or next epoch. READ samples ACTIVE once for readiness and copies that same zero into its header, avoiding a false in-flight report from a later rejected admission. A transient nonzero value at readiness returns EBUSY, which the helper may retry within the shared deadline. Its private copied Vec remains stable even if another owner thread subsequently STARTs.

Atomic admission and aggregate cache lines can contend and perturb timing. No CPU is pinned by the probe. Raw `ostd::arch::read_tsc()` deltas measure elapsed time, not CPU time, and provide no instruction-serialization guarantee. They may include interrupts, task switching, host descheduling, and migration. Different endpoint CPUs are flagged; migration away and back is unobserved. Interpretation needs synchronized invariant TSC and recorded frequency. Backwards stage timestamps are explicitly counted rather than subtracted as huge unsigned durations.

The daemon, control syscalls, and UDP clock bridge perturb the guest too. Correlation between a long timer batch and a host-interface gap motivates inspecting stages and counts; it does not itself prove timer CPU cost or historical regression causality. Calibration with a lowered threshold must remain separate from the five-millisecond diagnostic cohort.

## Checks before integration

Isolated injection checks use both exact frozen timer files plus real lib/prctl files, verify the enabled-only clock lookup and post-call outcome hooks, confirm no added Weak strong-count reads, and reject duplicate application. Python parsing and host C syntax checking use no guest execution. Rustfmt parses the Rust module. These are not a kernel typecheck, guest Nix gcc build, SMP runtime test, or latency experiment; those remain parent integration gates.
