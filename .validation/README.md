# Exclusion-loader validation

This fork-only branch validates the upstream `check -E` draft through the normal
Asterinas Nix package and initramfs build.
It does not include the pending
`/dev/fd` init repair or the wrapper preflight from the separate worktree.
The added workflow runs only in the user's fork on this validation branch.

Nix applies the patch after the inherited xfstests patch phase.
The preparation
script reverses only that patch on a copy of the packaged check script to obtain
the baseline.
Both versions retain the same packaged interpreter and helpers.
The raw suite is built once.
Adding guest fixtures changes the wrapper source
and initramfs, so the final build includes those fixtures through the normal
conformance Makefile.

The Linux matrix exercises extracted loading branches with the packaged Bash.
It temporarily removes only the disposable job container's `/dev/fd` symlink.
It is not a full Linux filesystem-suite run.

The Asterinas guest executes the actual baseline and patched `check` scripts.
It starts without `/dev/fd`, then creates the alias for the remaining cases.
It checks that generic/001 remains selected while generic/558 and generic/590
are excluded when appropriate.
It covers comments, empty files, missing files,
filenames with spaces, repeated -E, a final line without a newline, a large
list, and a producer that fails before or after partial output.
The unchanged
-e branch is a negative control showing that other process substitutions still
need their runtime environment.

Every full check invocation forces -n with an empty environment selector.
The scripts still prepare isolated tmpfs mounts, but do not execute dangerous
test bodies.
The guest gets no host block devices.
The job exposes only KVM
from the host and has no writable host /dev bind mount.
QEMU has a 300-second
limit, with separate limits for the suite build and kernel build/run.

A passing job requires explicit guest markers, expected selections, and expected
nonzero statuses for producer failures.
The QEMU exit status alone is not an
acceptance condition.
This workflow is an experiment, not an addition to the
normal regression suite or a claim of full conformance coverage.
