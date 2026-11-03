# Temporary timer batch detail and heap-pool probe v3

This diagnostic records elapsed timer-dispatch stages on SMP kernels without changing callback selection, execution, or drop ordering. It is for an isolated x86-64 guest. It does not establish the cause of previously observed tails. Kernel compilation and guest validation remain the parent's integration work.

`apply_batch_probe.py CHECKOUT` accepts only exact timer source SHA256s for base `e0d8b839f95f608c8a7e9c8942e01614a3b3b260` and fix `64848d3e0a23ff0903f4be485fb7750c1709a575`. First verify the complete plain variant identity. The script validates every injection anchor before writing, copies `batch_probe.rs`, adds its private module, adds the custom prctl branch, and injects equivalent hooks into the heap or BTreeMap timer implementation. It makes no commit. Reapplication fails; restore plain sources first.

Compile `batch_probe.c` with the original guest Nix gcc package, for example `-std=gnu11 -O2 -Wall -Wextra`. Run it as root after networking is ready. It checks the ABI, binds UDP `10.0.2.15:9998`, prints and flushes one `TIMER_BATCH_READY version=3 threshold_ns=<effective>` line, then stays in the same process. The original timer-perf and minimal Go binaries remain unchanged. The UDP interface is unauthenticated and belongs only on the isolated experiment network.

The optional helper argument `--threshold-ns N` sets a private threshold override. Zero or omission means 5,000,000 ns; 1 through 1,000,000,000 selects that threshold. Use a low override only for explicit event-path calibration, and preserve it in metadata. Production diagnostic cohorts must validate header word 16 equals 5,000,000. No wire layout changes with the override.

## UDP protocol

Every field is an unsigned 64-bit little-endian integer. Requests contain exactly five words (40 bytes):

| Word | Meaning |
| --- | --- |
| 0 | Magic `0x4153544241544348`. |
| 1 | Version 3. |
| 2 | Command: PING=0, CLOCK=1, START=2, FREEZE=3. |
| 3 | Caller nonce, echoed unchanged. |
| 4 | Case ID: zero for PING/CLOCK, nonzero for START, matching current case for FREEZE. |

Replies contain exactly twelve words (96 bytes): magic, version, echoed command, nonce, case ID, positive Linux errno (zero on success), helper TSC before processing, helper TSC after processing, frequency Hz, last observed epoch, successful dump bytes, and total EBUSY retries across FREEZE and READ.

PING and CLOCK both call kernel INFO. Use CLOCK round trips to bracket guest TSC against host timestamps; host and guest clocks are not assumed equal. START enables recording before replying. FREEZE disables admission, retries EBUSY on FREEZE and idempotent READ at 1 ms intervals within one shared two-second monotonic deadline, and writes `/var/tmp/timer-batch-<caseid>.bin` through a temporary file and rename before replying. Its TSC interval includes draining and file I/O and is unsuitable for low-latency calibration. A timeout leaves admission disabled and old-epoch writes preserved; another explicit FREEZE can retry after the host records the failure. Non-EBUSY control errors are reported immediately.

Malformed lengths produce EINVAL and a stderr report. Invalid magic/version produces EPROTO; unknown commands produce EINVAL. Partial fields in malformed packets are zero-padded for error replies. Validate reply size, magic, version, nonce, command, case, and errno before interpreting other fields. ABI failure is fatal at startup. The helper does not hide lost UDP packets or retry START. START is not idempotent: retrying after a lost successful reply yields EBUSY. Preserve that ambiguity. Choose unique case IDs; a later successful dump of the same case replaces its file.

Successful FREEZE reports 51,584 dump bytes. Export files via the existing base64 path after all workloads. Keep the daemon alive across cases: ownership belongs to its PID, and restarting it requires a fresh guest for this experiment.

## Kernel control ABI

Use `prctl(0x41534250, command, arg3, arg4, 0)`. Every command requires effective root. The first START establishes the owner PID. Other root PIDs may INFO but cannot START, FREEZE, or READ.

| Command | arg3 | arg4 | Return |
| --- | --- | --- | --- |
| INFO=0 | Eight-word buffer. | At least 64 bytes. | 64 bytes. |
| START=1 | Nonzero case ID. | Threshold override ns, 0 for default. | Zero. |
| FREEZE=2 | Matching case ID. | Zero. | Snapshot size, or EBUSY while admitted batches remain. |
| READ=3 | Snapshot buffer. | At least 51,584 bytes. | 51,584 bytes, only after successful FREEZE. |

INFO returns magic, version, header words (32), event words (34), capacity (128), frequency Hz, epoch, and instantaneous combined timer-and-heap admission count. START requires frozen admission and zero active count; after the initial epoch it also requires a prior successful quiescence acknowledgment. It increments epoch and resets aggregates and event commits. Epoch overflow returns EOVERFLOW. Threshold cycles are precomputed at START using ceiling division; hooks read the cached threshold rather than querying frequency. The helper rejects an uninitialized frequency at startup.

The control syscall lock serializes ownership, epoch transitions, and copying atomic records into a preallocated Vec. Allocation occurs before locking; `VmIo::write_slice` copies the private Vec to userspace after unlocking. Timer hooks do not acquire this lock, allocate, print, or disable preemption. Disabled admission does not touch or initialize the monotonic clock singleton.

## Binary snapshot

The timer block remains 4,384 native x86-64 little-endian u64 words: a 32-word header followed by 128 events of 34 words. A separate heap block follows it as described below. The complete file is 51,584 bytes. Unused slots are zero. Ignore slots beyond the stored count. Check each stored commit equals slot index plus one and its epoch matches the header.

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

Flag bit 0 marks any nonmonotonic TSC observation, including detail reads, bit 1 marks the skipped callback stage on an empty queue, and bit 2 marks differing entry/exit CPU IDs. Empty-queue collection still records an under-lock timestamp; its callback start equals batch end, preserving acquisition delays even if another CPU emptied the queue first.

Event words 0 through 19 retain their v1 meanings. Words 20 through 33 append seven pairs, each `(sum_cycles, max_cycles)`:

| Stage index | Event words | Stage name | Measured operation |
| --- | --- | --- | --- |
| 0 | 20, 21 | `clock_read` | The original `self.clock.read_time()`, under the queue lock. |
| 1 | 22, 23 | `cancelled_pop_and_drop` | Each cancelled queue pop, including the returned value's statement-end drop under the queue lock. |
| 2 | 24, 25 | `selected_pop` | Each selected queue pop, yielding the owned callback Arc. |
| 3 | 26, 27 | `vector_push` | Each push of that owned Arc into the existing callback Vec. |
| 4 | 28, 29 | `callback_timer_lock` | Each existing `timer.lock()` after a successful Weak upgrade. |
| 5 | 30, 31 | `callback_periodic_rearm` | Each existing `set_timeout(Timeout::After(interval))` for a nonzero interval. |
| 6 | 32, 33 | `registered_callback` | Each registered callback invocation, including destruction of its consumed TimerGuard before the call returns. |

These are elapsed TSC ticks, not nanoseconds. Sums are per admitted batch; maxima are per individual operation. A zero pair can mean no operation. Addition saturates at u64::MAX; there is no saturation expected within a bounded experiment. Header words and the fourteen aggregate statistics are unchanged. VERSION=3, HWORDS=32, EWORDS=34, CAPACITY=128, and the timer block size is 35,072 bytes. The appended heap block brings the complete dump to 51,584 bytes. The UDP request remains five words and the reply twelve words; its version word is now 3.

Every admitted batch tracks the preceding timestamp across both outer stages and detail pairs. Any backwards observation sets event flag bit 0 and increments the existing `bad_tsc_batches` count on completion. A bad batch contributes no duration aggregates; it may still emit through qualifying deadline lateness. The v3 decoder must reject bad-TSC data even when the six outer timestamps happen to be ordered, because the violation may exist only in a detail pair. Disabled batches perform no detail TSC read. Detail sums and maxima live only on the Batch stack; this adds no global statistic, allocation or lock to the hot hooks.

The callback's optional batch argument borrows only the existing recorder. Ktest calls use `None`. There is no added Arc clone, Weak upgrade or owner-count read. The selected pop is split from Vec push only to bracket them separately, retaining the same owned Arc and final ordering. Cancelled pop return values are dropped at their original statement boundary, before detail end and before queue unlock. Callback owner upgrade, timer lock, cancellation check, periodic rearm, body, guard and owner drops keep their original order.

No owner cache is inspected during collection: there is no added `Weak::strong_count()` read or Weak upgrade. The existing private callback method accepts an optional mutable batch reference and reports its original outcome: 0 for failed owner upgrade, 1 for cancellation after timer locking, and 2 after invoking the registered callback. Its upgrade, lock, cancellation check, callback, and local drops retain their original order. Other existing call sites ignore the result. The probe counts outcomes only after that call returns. Cancellation after selection is derived as `expired selections - failed upgrades - invoked callbacks`. Cancelled removals are separate and may include entries whose deadlines have not been reached.

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

## Detail interpretation and integration boundary

The queue clock, cancellation/drop, pop and Vec-push stages run under the existing IRQ-disabling queue guard. Ordinary local guest IRQs and preemption remain disabled there; host descheduling, KVM/VM-exit work and cross-vCPU resource contention can still contribute elapsed time. Callback detail stages use their original timer/guard rules after releasing the queue guard. Rearm includes its existing clock reads, allocation, cancellation and insertion. Registered-callback time may include the TimerGuard's drop and work performed by the callback; it is not a statement about user-visible application CPU cost.

The detail measurements do not partition total duration: the owner Weak upgrade, cancellation checks, loop bookkeeping, diagnostic accounting, guard release and other gaps remain outside these seven pairs. Outer stages already contain the detail reads/bookkeeping and must not be added to detail sums. Per-operation timestamp and local accounting overhead can materially perturb high-population batches. Final global aggregate/event publication still follows the outer end timestamp. Raw TSC reads remain unserialized; monotonicity checks do not establish synchronized invariant TSC or exclude migration away and back.

This directory prepares v3 timer and heap recorders, injector and helper sources. It supplies no Python decoder, exporter or build integration. The parent must update those to version 3, retain the 34-word timer events, append the heap block, and require 51,584 bytes, including detail-only bad-TSC handling and complete export framing. V1 and v2 sources, production changes and fork/CI inputs are untouched. No runtime or causal conclusion follows from preparing this diagnostic.

## V3 shared heap-pool block

V3 keeps the entire timer block layout and fourteen timer aggregate fields. Its version word becomes 3. At byte offset 35,072 it appends a 16-word heap header and 128 heap events of 16 words, totaling 16,512 additional bytes. Complete size is 6,448 words or 51,584 bytes. All words use the same native x86-64 little-endian encoding. Unused heap slots are zero in READ's private output vector.

| Heap header word | Meaning |
| --- | --- |
| 0 | Heap magic `0x4153544845415044`. |
| 1–4 | Version 3, header words 16, event words 16, capacity 128. |
| 5–7 | Qualifying heap event attempts, stored events, dropped events. |
| 8 | Calls with any bad/incomplete, returned-error or migration flag. |
| 9 | Completed admitted heap calls, including flagged calls. |
| 10–11 | Sum and maximum acquisition-wait cycles among unflagged calls. |
| 12–13 | Sum and maximum pool-hold cycles among unflagged calls. |
| 14 | Attempted `global_pool.alloc` calls in admitted refill scopes. |
| 15 | Attempted `global_pool.dealloc` calls in admitted drain scopes. |

| Heap event word | Meaning |
| --- | --- |
| 0 | Release commit, slot index plus one. |
| 1 | Epoch shared with the timer header and case. |
| 2 | Entry racy logical CPU ID. |
| 3 | Kind: 0 refill, 1 drain. |
| 4 | Slot size class in bytes, from 8 through 2048. |
| 5 | Start TSC before acquiring GLOBAL_POOL. |
| 6 | Acquired TSC after GLOBAL_POOL.lock returns. |
| 7 | Before-release TSC after the original allocator body completes. |
| 8 | Released TSC immediately after explicit drop of the pool guard. |
| 9–10 | This local size-class cache's bytes before/after the original body. |
| 11 | Number of attempted pool operations of this kind, including failed attempts. |
| 12 | Flags: bit 0 bad TSC or missing boundary, bit 1 returned error, bit 2 different endpoint CPUs. |
| 13 | Exit racy logical CPU ID. |
| 14–15 | Reserved zero. |

A heap scope qualifies if its flags are nonzero or its valid `released_tsc - start_tsc` reaches the same cached threshold as timer events. The first 128 qualifying scopes are retained without overwriting; aggregate counters continue after overflow. Publication slots are ordered by completion, not entry. Check each commit and epoch. A rejected admission writes no statistics or records. Flagged calls remain individually available for diagnosis, but do not contribute aggregate duration sums/maxima. Attempt counts include every original pool call made by an admitted wrapper; they do not identify successful allocation count or the underlying failure reason. A refill may return success from its original fallback even when a pool allocation failed, so no returned-error flag is set in that case.

Acquisition-wait means acquired minus start. Scope construction and movement occur before a separate `before_lock()` samples entry CPU and start TSC through the existing optional scope immediately before GLOBAL_POOL.lock. It includes the existing lock/IRQ-guard acquisition instructions and measurement-call overhead, not solely contention. Pool-hold means before-release minus acquired and includes the original refill/drain body and local pre-release diagnostic bookkeeping. Immediately after explicit guard drop, `released()` stamps through the existing optional scope before it is moved into `finish()`. Finish only marks completion and publishes. Released minus before-release measures the guard-release gap; it does not include moving Scope into finish. These are elapsed TSC intervals; none establishes CPU cost. The local cache is still borrowed and its outer IRQ guard is still held when finish publishes the event, so root publication adds IRQ-off overhead after the measured released timestamp.

### Hook installation and freeze

The lower allocator crate exposes the temporary `timer_heap_probe` module with one `spin::Once<Hooks>`. Its two function pointers are installed by the kernel only in the first successful START, after reset and before ENABLED publication. Early boot never installs them. An uninstalled or disabled hook returns without TSC reads. `Once::get` is the only lookup in the lower hot path; the hooks have no allocation, output, new measurement lock, timer reference or callback-reference operation. Scope fields are stack-local. The end hook updates only fixed root atomic arrays/counters and never takes CONTROL_LOCK.

The root begin hook uses the identical ENABLED-load, ACTIVE-increment, ENABLED-recheck protocol as timer batches. An accepted heap scope holds one ACTIVE reference through final statistics and release publication. Timer scopes and heap scopes may nest and both count independently. FREEZE disables both admission types and succeeds only when the shared ACTIVE count is zero. START resets heap statistics, commit words and NEXT alongside timer state, without resetting ACTIVE. READ copies both blocks under the existing control lock to its private preallocated vector; userspace copying remains outside that lock. INFO remains eight words, with version 3 and the combined active count.

### Allocator path preservation

Only ObjectCache refill and drain paths change. The existing cache-hit alloc/dealloc returns and large allocation paths stay untouched. Refill retains its original pool loop, final pool attempt and local-cache fallback in the same order. Drain runs its original initial deallocation and loop inside an inline closure so every original `?` still stops at the same failure, without new fallback or retries. Cache byte accounting retains its original position relative to each successful operation. The existing pool guard is declared after the optional scope, explicitly dropped after the original result is materialized, and dropped before the end hook. Begin leaves all timing boundaries zero; the root also flags any zero or nonmonotonic boundary. A scope dropped without normal finish records a flagged missing-boundary observation; stack unwinding drops the subsequently declared pool guard first. Kernel abort/panic termination cannot be turned into a completed experiment by this guard.

The injector also adds `spin.workspace = true` to the heap allocator's Cargo.toml. This existing workspace package supplies safe Once; there is no unsafe function-pointer cast, RCU allocation or OSTD API change. This single dependency edge was explicitly authorized for the temporary diagnostic. The parent must preserve the original lock, freeze a v3 lock with only that edge added, and reject any package/version/checksum drift. The injector itself does not resolve or rewrite locks.

Exact pristine SHA256 gates cover each frozen timer variant and the shared allocator.rs, allocator lib.rs, allocator Cargo.toml, kernel lib.rs and kernel prctl.rs. Every anchor and new-module absence check is completed before writes. `pristine-sha256.json` records those inputs; `source-sha256.json` records this package. Applying the injector adds lower `timer_heap_probe.rs`, its public module declaration, allocator wrappers, the existing timer diagnostic module/prctl dispatch and their timer hooks, plus the one Cargo edge. It makes no commit and rejects duplicate application.

### Scope and required integration

Heap calls anywhere in the guest are observed while enabled; a recorded pool scope is not automatically owned by a Redis request, a timer dispatch or the thread whose host scheduling sample appears nearby. Correlate its exact timestamps and logical CPU with enclosing timer stages only under the documented clock/CPU assumptions. Fast local-cache operations, large allocation paths, upstream slab internals, page allocation and work after the released timestamp are not independently measured by this block.

This package supplies no v3 Python decoder, export framing, Nix build orchestration or lock rewrite. C helper request/reply widths, case-ID semantics, root requirement, PID ownership and control commands remain unchanged except version 3 and the 51,584-byte snapshot. It checks the appended heap magic/version/shape before dumping. The parent must compile both injected variants, validate both paths and error/admission/freeze behavior, freeze source/lock/helper identities, update complete framing/decoding and keep calibration separate from diagnostic cohorts. Source preparation alone establishes no cause for the previously observed tails.
