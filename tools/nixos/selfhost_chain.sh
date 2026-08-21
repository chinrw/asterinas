#!/bin/bash

# SPDX-License-Identifier: MPL-2.0

# Drive the self-host chain against the installed AsterNixOS disk: boot L1,
# shallow-clone the requested revision inside it, build the kernel there, and
# boot the result as a nested TCG guest (L2). See
# book/src/distro/building-on-asterinas.md.
#
# Run from inside the Nix dev shell, after `make iso` and `make run_iso` have
# produced target/nixos/asterinas.img. The L1 guest needs KVM, network
# access, and SELFHOST_MEM (default 32G) of RAM; the guest substitutes the
# dev-shell closure from the caches preconfigured in the image plus this
# machine's own store, which is served to it for the packages public caches
# do not carry.
#
# Environment:
#   SELFHOST_REF       (required) branch, tag, or full commit hash to build inside L1
#   SELFHOST_REPO_URL  repository to fetch from (default: this checkout, served to L1)
#   SELFHOST_MEM       L1 guest RAM        (default 32G)
#   SELFHOST_SMP       L1 guest vCPUs      (default 8)
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
cd "${ASTERINAS_DIR}"

cleanup() {
    # L1 runs in its own session (setsid), so its process group is make + QEMU.
    [ -n "${L1_PGID:-}" ] && { kill -TERM -- "-${L1_PGID}" 2>/dev/null; sleep 4; kill -KILL -- "-${L1_PGID}" 2>/dev/null; }
    [ -n "${SERVE_PID:-}" ] && kill "${SERVE_PID}" 2>/dev/null
    [ -f "${RUN_DIR}/git-daemon.pid" ] && kill "$(cat "${RUN_DIR}/git-daemon.pid")" 2>/dev/null
}
trap cleanup EXIT

# Serve this machine's store to the guest: the caches preconfigured in the
# image cover nixpkgs, but not the Asterinas QEMU/GRUB/OVMF packages the L2
# step needs, and this store realized all of them for `make iso` already.
# The guest reaches this host at 10.0.2.2 under QEMU user networking.
nix run --inputs-from . nixpkgs#nix-serve -- --port 18081 >/dev/null 2>&1 &
SERVE_PID=$!
SUBST_OPTS='--option http-connections 1 --option extra-substituters http://10.0.2.2:18081'

# Serve this checkout to the guest the same way: L1's own link to GitHub drops
# mid-transfer (TLS resets), and the revision to build is right here anyway.
# The -c setting lets L1 fetch the exact commit hash the workflow passes.
git -c uploadpack.allowReachableSHA1InWant=true daemon --export-all --reuseaddr --detach \
    --listen=127.0.0.1 --port=9418 --base-path="$(dirname "${ASTERINAS_DIR}")" \
    --pid-file="${RUN_DIR}/git-daemon.pid" "${ASTERINAS_DIR}"

fail() {
    echo "selfhost_chain: $1" >&2
    echo "--- last 80 lines of the L1 console ---" >&2
    tail -80 "${LOG}" >&2 || true
    exit 1
}

# Boot L1 headless with the console on our FIFO.
exec 9<>"${FIFO}"
setsid make run_nixos MEM="${SELFHOST_MEM}" SMP="${SELFHOST_SMP}" \
    TARGET_ARCH=x86_64 <"${FIFO}" >"${LOG}" 2>&1 &
L1_PGID=$!

send() { # one console line; typed slowly so the virtio console keeps up
    local s=$1 i
    for ((i = 0; i < ${#s}; i++)); do
        printf '%c' "${s:$i:1}" >"${FIFO}"
        sleep 0.015
    done
    printf '\n' >"${FIFO}"
    sleep 2
}

wait_for() { # regex timeout-seconds label
    local pat=$1 t=$2 label=$3 i=0
    until grep -aqE "${pat}" "${LOG}" 2>/dev/null || [ "${i}" -ge "${t}" ]; do
        sleep 10
        i=$((i + 10))
        grep -aq 'Uncaught panic' "${LOG}" 2>/dev/null \
            && fail "kernel panic while waiting for ${label}"
    done
    grep -aqE "${pat}" "${LOG}" 2>/dev/null || fail "timeout waiting for ${label}"
}

wait_for 'automatic login' 600 "the L1 login shell"
sleep 20

if [ -n "${SELFHOST_L1_PROXY:-}" ]; then
    send 'export https_proxy='"${SELFHOST_L1_PROXY}"' http_proxy='"${SELFHOST_L1_PROXY}"
    send 'export all_proxy='"${SELFHOST_L1_PROXY}"' no_proxy=10.0.2.2,127.0.0.1'
fi

# git init + fetch rather than clone, so branches and bare commits work alike.
send 'mkdir -p /work/asterinas && cd /work/asterinas && git init -q; echo INIT-rc=$?'
wait_for 'INIT-rc=[0-9]' 60 "the work repository"
grep -aq 'INIT-rc=0' "${LOG}" || fail "git init failed"
send 'git fetch --depth 1 '"${SELFHOST_REPO_URL}"' '"${SELFHOST_REF}"'; echo FETCH-rc=$?'
wait_for 'FETCH-rc=[0-9]' 1200 "the shallow fetch"
grep -aq 'FETCH-rc=0' "${LOG}" || fail "shallow fetch failed"
send 'git checkout -q FETCH_HEAD; rc=$?; sync; echo CO-rc=$rc'
wait_for 'CO-rc=[0-9]' 600 "the checkout"
grep -aq 'CO-rc=0' "${LOG}" || fail "checkout failed"

send 'nix develop '"${SUBST_OPTS}"' --command make kernel TARGET_ARCH=x86_64; rc=$?; sync; echo KBUILD-rc=$rc'
wait_for 'KBUILD-rc=[0-9]' 7200 "the in-guest kernel build"
grep -aq 'KBUILD-rc=0' "${LOG}" || fail "kernel build inside L1 failed"

send 'nix develop '"${SUBST_OPTS}"' --command make run_kernel TARGET_ARCH=x86_64 ENABLE_KVM=0 NETDEV=none QEMU_DISPLAY=none MEM=2G SMP=1 AUTO_TEST=boot; rc=$?; sync; echo L2RUN-rc=$rc'
wait_for 'L2RUN-rc=[0-9]' 2400 "the nested L2 boot"
grep -aq 'L2RUN-rc=0' "${LOG}" || fail "nested L2 run failed"
grep -aq 'Successfully booted' "${LOG}" || fail "L2 ran but never printed the boot marker"

send 'sync'
sleep 10
echo "selfhost_chain: L2 booted successfully from a kernel built inside L1"
