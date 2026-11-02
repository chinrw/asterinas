# Timer tail diagnosis

This fork-only workflow compares the frozen base and lifecycle-repair kernels on one EPYC runner. It is a diagnostic experiment, not a performance acceptance run. Each variant boots a fresh guest. The exact CPU model, dependency locks, kernel binaries and workload records are retained.

Redis fixed-rate windows last 30 seconds at 1,000 requests/second with 20 persistent connections. nginx windows remain five seconds at 200 requests/second. The existing closed-loop workloads and preceding full workload order remain. Background churn requests 5,000 cycles/second; periodic expiry uses 128 timerfds at 20ms. Failed producer coverage or observation validation remains a failed sample.

The observer samples host thread counters every 10ms, verifies complete application-byte coverage in the packet capture, and records client garbage collections. Scheduler wait accounting must be enabled on the owned ephemeral runner. Completed request bytes must appear in the capture before tcpdump is stopped. This verifies application-stream coverage, not capture of every ACK or retransmission.

A request pending for at least 20ms requests one guest snapshot per case. QMP maps vCPU indices to the owned process's threads. The observer first records available host kernel stacks, syscalls and wait channels for those threads, then pauses the guest to read general registers and kernel stack words. It resumes the guest in a finally path, reconnecting if the original QMP reader timed out. Register collection has a 100ms work budget and a 250ms per-call timeout; actual pause timestamps are always recorded. Only kernel addresses are used for stack reads. Raw stack words are not an unwound backtrace.

The snapshot is intrusive. Analyze the request's pre-trigger delay and correlate snapshot times with observed packet arrivals. Latency after the stop request includes the diagnostic pause and must not be reported as natural latency. A missing or late snapshot does not establish that the guest was healthy. Source attribution requires the uploaded ELF for the measured variant. These results must remain separate from earlier five-second, non-QMP cohorts.

All changes in this branch are temporary diagnostics. The timer implementation, scheduler policy and callback budget are unchanged.
