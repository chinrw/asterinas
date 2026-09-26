#!/bin/bash

# SPDX-License-Identifier: MPL-2.0

# Drive the self-host chain on an installed Asterinas NixOS disk: boot it as
# L1, fetch the requested revision inside it, build the kernel there, and boot
# that kernel as a nested L2 guest. The steps inside L1 follow
# book/src/kernel/building-on-asterinas.md.
#
# Run it in the Nix development shell after `make iso` and `make run_iso` have
# produced target/nixos/asterinas.img. L1 needs network access. It downloads
# the development shell from the binary caches configured in the image. One
# of them, aster-nixos-dev, carries the QEMU, GRUB, and OVMF packages that
# Asterinas cannot build yet.
#
# Environment:
#   SELFHOST_REF       (required) branch, tag, or full commit hash to build in L1
#   SELFHOST_REPO_URL  repository to fetch from (default: this checkout, served to L1)
#   SELFHOST_MEM       L1 memory (default 32G)
#   SELFHOST_SMP       L1 vCPUs (default 8)
#   SELFHOST_L1_PROXY  optional HTTP proxy exported inside L1

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ASTERINAS_DIR=$(realpath "${SCRIPT_DIR}/../..")
RUN_DIR="${ASTERINAS_DIR}/target/selfhost-chain"
FIFO="${RUN_DIR}/console-in.fifo"
LOG="${RUN_DIR}/l1-console.log"

SELFHOST_REPO_URL=${SELFHOST_REPO_URL:-git://10.0.2.2/$(basename "${ASTERINAS_DIR}")}
SELFHOST_REF=${SELFHOST_REF:?SELFHOST_REF (branch or commit) is required}
SELFHOST_MEM=${SELFHOST_MEM:-32G}
SELFHOST_SMP=${SELFHOST_SMP:-8}

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
    [ -f "${RUN_DIR}/git-daemon.pid" ] && kill "$(cat "${RUN_DIR}/git-daemon.pid")" 2>/dev/null
}
trap cleanup EXIT

# Serve this checkout to L1, which reaches this host at 10.0.2.2 under QEMU
# user networking. Its own link to GitHub can drop mid-transfer, and the
# revision to build is here anyway. The -c setting lets L1 fetch a bare
# commit hash, which is what the workflow passes.
git -c uploadpack.allowReachableSHA1InWant=true daemon --export-all --reuseaddr --detach \
    --listen=127.0.0.1 --port=9418 --base-path="$(dirname "${ASTERINAS_DIR}")" \
    --pid-file="${RUN_DIR}/git-daemon.pid" "${ASTERINAS_DIR}"

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

wait_for() { # regex timeout-seconds label
    local pat=$1 t=$2 label=$3 i=0
    until since_mark | grep -aqE "${pat}" || [ "${i}" -ge "${t}" ]; do
        sleep 10
        i=$((i + 10))
        grep -aq 'Uncaught panic' "${LOG}" 2>/dev/null \
            && fail "kernel panic while waiting for ${label}"
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

if [ -n "${SELFHOST_L1_PROXY:-}" ]; then
    send "export https_proxy=${SELFHOST_L1_PROXY} http_proxy=${SELFHOST_L1_PROXY}"
    send "export all_proxy=${SELFHOST_L1_PROXY} no_proxy=10.0.2.2,127.0.0.1"
fi

# git init + fetch rather than clone, so branches and bare commits work alike.
run_step INIT 60 "creating the work repository" \
    'mkdir -p /work/asterinas && cd /work/asterinas && git init -q'
run_step FETCH 1200 "fetching ${SELFHOST_REF}" \
    "git fetch --depth 1 ${SELFHOST_REPO_URL} ${SELFHOST_REF}"
run_step CO 600 "checking out the source" 'git checkout -q FETCH_HEAD && git log -1 --format=%H'

# The flake declares the Asterinas caches in nixConfig, and Nix stops to ask
# about them on a terminal. The image already lists the same caches, so
# accepting changes nothing but keeps the console from waiting for an answer.
# One download at a time makes a known Asterinas panic less likely: a TCP
# receive that page-faults on the user buffer can sleep in atomic mode.
# With one connection, the other requests wait in curl's queue, and Nix counts
# that wait against connect-timeout (15 s by default), so turn the limit off.
NIX_DEVELOP='nix develop --accept-flake-config --option http-connections 1 --option connect-timeout 0'

# Count the store paths that the development shell adds, by the key that
# signed them. A path built inside L1 has no signature, so the counts show
# what came from each cache and what L1 had to build itself.
read -r -d '' COUNT_ADDED_PATHS <<'EOF'
nix path-info --all --sigs | sort | comm -13 /tmp/store-before - | awk '{ if (NF == 1 || $2 == "ultimate") print "BUILT " $1; else { split($2, k, ":"); print "SIGNED " k[1] } }' | sort | uniq -c
EOF

run_step STORE0 600 "recording the store contents" \
    'nix path-info --all --sigs | sort >/tmp/store-before'
# The progress bar would redraw on the serial console many times a second,
# so print plain log lines instead.
run_step DEVSHELL 5400 "downloading the development shell" \
    "${NIX_DEVELOP} --log-format raw --command true && sync"
run_step STORE1 600 "counting the paths the development shell added" "${COUNT_ADDED_PATHS}"
since_mark | tr -d '\r' | grep -aoE '[0-9]+ (SIGNED|BUILT) .*' | sed 's/^/selfhost_chain: added /'

run_step KBUILD 7200 "building the kernel in L1" \
    "${NIX_DEVELOP} --command make kernel && sync"

# With AUTO_TEST=boot, `make run_kernel` should exit once L2 has booted, but
# on current Asterinas it does not return: signal-hook in cargo-osdk drains a
# socket with recv(MSG_DONTWAIT), and Asterinas ignores that flag and blocks.
# Until that is fixed, the boot marker from L2 is the success signal.
log "start: booting the L1-built kernel in L2"
mark
send "${NIX_DEVELOP} --command make run_kernel ENABLE_KVM=0 NETDEV=none QEMU_DISPLAY=none MEM=2G AUTO_TEST=boot; echo L2RUN-rc=\$?"
wait_for 'Successfully booted|L2RUN-rc=[0-9]' 2400 "the nested L2 boot"
since_mark | grep -aq 'Successfully booted' || fail "L2 exited without printing the boot marker"
log "done: L2 booted successfully from a kernel built inside L1"
