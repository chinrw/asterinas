<!-- SPDX-License-Identifier: MPL-2.0 -->
# Asterinas and Linux on the same runner

Select `linux-control` in the registered workflow to compare the plain lifecycle repair with Linux on one EPYC runner. The builder checks the frozen source and original dependency lock, rejects stale setup/image/output paths, and builds the Asterinas kernel and userland image once. Linux boots a separate pinned kernel and initrd against a fresh copy of the same disk bytes. The original image is checked again after all guests finish.

Four fresh guests run in Asterinas, Linux, Linux, Asterinas order. Each executes 10,000 short HTTP requests, 100,000 Redis SET and GET requests each, then 20 seconds of fixed-rate Redis traffic at 1,000 requests per second. A churn producer requests 5,000 cycles per second throughout the history and measurement and must achieve at least 4,500. The unchanged Go helper must finish within its existing deadline. All samples, including failures, are retained.

Both OSes use project QEMU 10.2.1, OVMF, four vCPUs, eight GiB, and the same TAP/vhost settings. Linux needs a separate module/initrd bootstrap, fixed IPv4 setup and console newline initialization. Its DHCP and NIC IPv6 are disabled before measurement; network state is checked afterward. Guest logging is settled before the workload. Temporary overlays protect auxiliary disk backing files. The root/ESP copy, existing helper binaries and application setup remain unchanged.

The controller checks complete request IDs, schedule/start/finish ordering, producer coverage, Go identity, QMP target identity, packet byte coverage, trace windows, and cleanup. This control has no timer/heap probe or perf recorder. Raw logs, packet traces, kernel/boot identities, locks, source snapshots and the actual host model are uploaded to the fork. The fixed-rate end-to-end metric includes client-side queueing; inspect started-to-finished and wire intervals before attributing small differences to an OS.

This history control is distinct from the full performance protocol. Comparing two kernels changes their networking, clock, interrupt and scheduling behavior together, so it cannot identify a timer-queue cause by itself. The source remains candidate 64848d3e0 on frozen base e0d8b839f; these runs are not a later-main revalidation. No upstream publication is part of this workflow.
