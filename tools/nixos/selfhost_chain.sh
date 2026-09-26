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
# The first `nix develop` downloads the development shell, so this step
# covers both the download and the build.
run_step KBUILD 14400 "building the kernel in L1" \
    'cd /work/asterinas && nix develop --accept-flake-config --command make kernel && sync'

# With AUTO_TEST=boot, `make run_kernel` should exit once L2 has booted, but
# on current Asterinas it does not return: signal-hook in cargo-osdk drains a
# socket with recv(MSG_DONTWAIT), and Asterinas ignores that flag and blocks.
# Until that is fixed, the boot marker from L2 is the success signal.
log "start: booting the L1-built kernel in L2"
mark
send "nix develop --accept-flake-config --command make run_kernel ENABLE_KVM=0 NETDEV=none QEMU_DISPLAY=none MEM=2G AUTO_TEST=boot; echo L2RUN-rc=\$?"
wait_for 'Successfully booted|L2RUN-rc=[0-9]' 2400 "the nested L2 boot"
since_mark | grep -aq 'Successfully booted' || fail "L2 exited without printing the boot marker"
log "done: L2 booted successfully from a kernel built inside L1"
