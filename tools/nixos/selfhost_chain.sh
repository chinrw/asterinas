#!/bin/bash

# SPDX-License-Identifier: MPL-2.0

# Run the steps of book/src/distro/building-on-asterinas.md on an installed
# Asterinas NixOS disk: boot it as L1, clone the source inside it, build the
# kernel there, and boot that kernel as a nested L2 guest.
#
# Run it in the Nix development shell after `make iso` and `make run_iso` have
# produced target/nixos/asterinas.img. L1 needs network access to clone the
# source and to download the development shell.
#
# Environment:
#   SELFHOST_REPO    GitHub repository to clone in L1 (default asterinas/asterinas)
#   SELFHOST_BRANCH  branch to clone (default: the repository's default branch)
#   SELFHOST_MEM     L1 memory (default 40G, as in the Book)
#   SELFHOST_SMP     L1 vCPUs (default 4, as in the Book)

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ASTERINAS_DIR=$(realpath "${SCRIPT_DIR}/../..")
RUN_DIR="${ASTERINAS_DIR}/target/selfhost-chain"
FIFO="${RUN_DIR}/console-in.fifo"
LOG="${RUN_DIR}/l1-console.log"

SELFHOST_REPO=${SELFHOST_REPO:-asterinas/asterinas}
SELFHOST_BRANCH=${SELFHOST_BRANCH:-}
SELFHOST_MEM=${SELFHOST_MEM:-40G}
SELFHOST_SMP=${SELFHOST_SMP:-4}

# The repository and branch are typed into the L1 shell, so accept only
# characters that the shell reads literally.
[[ ${SELFHOST_REPO} =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] \
    || { echo "unsupported SELFHOST_REPO: ${SELFHOST_REPO}" >&2; exit 1; }
[[ ${SELFHOST_BRANCH} =~ ^[A-Za-z0-9._/-]*$ ]] \
    || { echo "unsupported SELFHOST_BRANCH: ${SELFHOST_BRANCH}" >&2; exit 1; }

# E2 logger experiment (temporary). Each window re-fetches the flake inputs
# and re-queries the narinfos in a fresh store, which is where the TCP
# atomic-mode panics happened. The arms differ only in Nix's logger:
#   raw      SimpleLogger, no progress lock
#   bar      ProgressBar on the tty, so the draw thread writes to the console
#   barpipe  ProgressBar with stderr piped through cat, so isTTY is false and
#            the draw thread exits while the progress lock is still taken
SELFHOST_LOG_FMT=${SELFHOST_LOG_FMT:-bar}
SELFHOST_LOOP_BUDGET=${SELFHOST_LOOP_BUDGET:-7200}
case ${SELFHOST_LOG_FMT} in
raw) NIX_LOG='--log-format raw' NIX_REDIR='>/dev/null' ;;
bar) NIX_LOG='--log-format bar' NIX_REDIR='>/dev/null' ;;
barpipe) NIX_LOG='--log-format bar' NIX_REDIR='2>&1 >/dev/null | cat' ;;
*) echo "unsupported SELFHOST_LOG_FMT: ${SELFHOST_LOG_FMT}" >&2; exit 1 ;;
esac
[[ ${SELFHOST_LOOP_BUDGET} =~ ^[0-9]+$ ]] \
    || { echo "unsupported SELFHOST_LOOP_BUDGET: ${SELFHOST_LOOP_BUDGET}" >&2; exit 1; }

mkdir -p "${RUN_DIR}"
rm -f "${LOG}" "${FIFO}"
mkfifo "${FIFO}"
cd "${ASTERINAS_DIR}" || exit 1

log() {
    echo "selfhost_chain: [$(date -u +%H:%M:%S)] $*"
}

cleanup() {
    # L1 runs in its own session (setsid), so its process group is make + QEMU.
    [ -n "${L1_PGID:-}" ] && { kill -TERM -- "-${L1_PGID}" 2>/dev/null; sleep 4; kill -KILL -- "-${L1_PGID}" 2>/dev/null; }
}
trap cleanup EXIT

fail() {
    log "$1" >&2
    echo "--- last 80 lines of the L1 console ---" >&2
    tail -80 "${LOG}" >&2 || true
    exit 1
}

# tools/qemu_args.sh draws host-forward ports from 1024-65535. If one of them
# is already taken by an outgoing connection (ephemeral range 32768-60999),
# QEMU refuses to start. The chain never uses the forwards, so pin them lower.
export SSH_PORT=${SSH_PORT:-20022} NGINX_PORT=${NGINX_PORT:-20023} \
    REDIS_PORT=${REDIS_PORT:-20024} IPERF_PORT=${IPERF_PORT:-20025} \
    LMBENCH_TCP_LAT_PORT=${LMBENCH_TCP_LAT_PORT:-20026} \
    LMBENCH_TCP_BW_PORT=${LMBENCH_TCP_BW_PORT:-20027} MEMCACHED_PORT=${MEMCACHED_PORT:-20028}

log "booting L1 with MEM=${SELFHOST_MEM} SMP=${SELFHOST_SMP}"
exec 9<>"${FIFO}"
setsid make run_nixos MEM="${SELFHOST_MEM}" SMP="${SELFHOST_SMP}" \
    <"${FIFO}" >"${LOG}" 2>&1 &
L1_PGID=$!

send() { # one console line, typed slowly so the virtio console keeps up
    local s=$1 i
    for ((i = 0; i < ${#s}; i++)); do
        printf '%c' "${s:$i:1}" >"${FIFO}"
        sleep 0.015
    done
    printf '\n' >"${FIFO}"
    sleep 2
}

# Console output since the last `mark`. Each wait looks only at the output of
# its own step, so an earlier line cannot satisfy a later check.
mark() {
    MARK=$(stat -c %s "${LOG}")
}
since_mark() {
    tail -c "+$((MARK + 1))" "${LOG}" 2>/dev/null
}

# Not every L1 failure prints a panic line: a kernel heap abort or a triple
# fault just ends QEMU. Watching the process catches those without waiting
# for the step timeout.
l1_alive() {
    local state
    state=$(ps -o stat= -p "${L1_PGID}" 2>/dev/null) || return 1
    [[ ${state} != Z* ]]
}

wait_for() { # regex timeout-seconds label
    local pat=$1 t=$2 label=$3 i=0
    until since_mark | grep -aqE "${pat}" || [ "${i}" -ge "${t}" ]; do
        sleep 10
        i=$((i + 10))
        grep -aq 'Uncaught panic' "${LOG}" 2>/dev/null \
            && fail "kernel panic while waiting for ${label}"
        l1_alive || fail "L1 exited while waiting for ${label}"
    done
    since_mark | grep -aqE "${pat}" || fail "timeout waiting for ${label}"
}

run_step() { # tag timeout-seconds label command
    local tag=$1 t=$2 label=$3 cmd=$4
    log "start: ${label}"
    mark
    send "${cmd}; echo ${tag}-rc=\$?"
    wait_for "${tag}-rc=[0-9]" "${t}" "${label}"
    since_mark | grep -aq "${tag}-rc=0" || fail "${label} failed"
    log "done: ${label}"
}

MARK=0
wait_for 'automatic login' 900 "the L1 login shell"
sleep 20
log "L1 logged in"

# The commands below are the Book's. The Book enters the development shell
# and types commands in it, while this script runs each command through
# `nix develop --command`, so that every step reports its own exit status.
run_step CLONE 1200 "cloning ${SELFHOST_REPO} ${SELFHOST_BRANCH}" \
    "git clone --depth 1 ${SELFHOST_BRANCH:+--branch ${SELFHOST_BRANCH} }https://github.com/${SELFHOST_REPO} /work/asterinas && git -C /work/asterinas log -1 --format=%H"
# The barpipe arm pipes nix into cat, and `|| break` must see nix's status.
send 'set -o pipefail'

# Removing ~/.cache/nix drops the fetcher and eval caches, and /tmp/s is a new
# store, so every window downloads the flake inputs and queries the narinfos
# again. The loop stops at the first failing window, and `test` reports
# whether all ten windows passed.
WINDOW_CMD="rm -rf ~/.cache/nix /tmp/s; nix build --store /tmp/s --accept-flake-config ${NIX_LOG} --dry-run .#devShells.x86_64-linux.default ${NIX_REDIR} || break"
LOOP_CMD="cd /work/asterinas && n=0 && for i in \$(seq 10); do ${WINDOW_CMD}; n=\$i; echo WIN=\$i; done; test \$n = 10"

# One window takes several minutes inside L1, so poll every 2 s and log the
# host time of each window boundary. With the kernel timestamp of a panic,
# these times place the panic within its window.
log "start: E2 loop, log format ${SELFHOST_LOG_FMT}, budget ${SELFHOST_LOOP_BUDGET} s"
mark
SNAP="${RUN_DIR}/loop-snapshot.log"
send "${LOOP_CMD}; echo LOOP-rc=\$?"
# send sleeps 2 s after the newline that starts the loop.
LOOP_START=$((SECONDS - 2))
WIN_START=${LOOP_START}
SEEN=0
PREV_OFF=0
log "window 1 started"
while :; do
    since_mark >"${SNAP}"
    mapfile -t WINS < <(grep -aboE 'WIN=[0-9]+' "${SNAP}" | cut -d: -f1)
    while [ "${SEEN}" -lt "${#WINS[@]}" ]; do
        off=${WINS[SEEN]}
        seg=$(tail -c "+$((PREV_OFF + 1))" "${SNAP}" | head -c "$((off - PREV_OFF))" | tr -d '\0')
        overlay=$(grep -ao 'oxalica/rust-overlay' <<<"${seg}" | wc -l)
        sources=$(grep -aoE "fetching source from|-source' from" <<<"${seg}" | wc -l)
        summary=$(tr '\r' '\n' <<<"${seg}" | grep -aoE 'these [0-9]+ paths will be fetched' | tail -1)
        SEEN=$((SEEN + 1))
        log "window ${SEEN} done after $((SECONDS - WIN_START)) s: rust-overlay mentions=${overlay}, source fetches=${sources}, ${summary:-no fetch summary}"
        # The progress bar shows each input download, so a bar window without
        # one means the loop is not re-fetching the inputs.
        [ "${SELFHOST_LOG_FMT}" = bar ] && [ "$((overlay + sources))" -eq 0 ] \
            && fail "window ${SEEN} showed no flake-input download"
        PREV_OFF=${off}
        WIN_START=${SECONDS}
        [ "${SEEN}" -lt 10 ] && log "window $((SEEN + 1)) started"
    done
    # Count finished windows before checking for a panic, so that a panic
    # printed in the same poll as WIN=k is charged to window k+1.
    grep -aq 'Uncaught panic' "${LOG}" \
        && fail "kernel panic in window $((SEEN + 1)), $((SECONDS - WIN_START)) s after it started, after ${SEEN} complete windows"
    l1_alive || fail "L1 exited in window $((SEEN + 1)) after ${SEEN} complete windows"
    grep -aqE 'LOOP-rc=[0-9]' "${SNAP}" && break
    if [ "$((SECONDS - LOOP_START))" -ge "${SELFHOST_LOOP_BUDGET}" ]; then
        log "done: time budget reached in window $((SEEN + 1)) after ${SEEN} complete windows, no panic"
        exit 0
    fi
    sleep 2
done
grep -aq 'LOOP-rc=0' "${SNAP}" || fail "the loop stopped after ${SEEN} complete windows"
log "done: ${SEEN} windows without a panic"
