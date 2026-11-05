#!/bin/bash

# SPDX-License-Identifier: MPL-2.0

set -e
set -o pipefail

BENCHMARK_PATH=$1
ASTERINAS_GUEST_CMD=$2
LINUX_GUEST_CMD=$3
ASTERINAS_OUTPUT=$4
LINUX_OUTPUT=$5
BENCHMARK_NAME=$6
CHECK_GUEST_EXIT=${7:-false}
case "$CHECK_GUEST_EXIT" in
    true|false) ;;
    *) echo "Invalid check_guest_exit: $CHECK_GUEST_EXIT" >&2; exit 1 ;;
esac
ARTIFACT_ROOT=${BENCHMARK_ARTIFACT_DIR:-"$PWD/target/benchmark-artifacts/$BENCHMARK_NAME"}
mkdir -p "$ARTIFACT_ROOT"
ARTIFACT_ROOT=$(cd "$ARTIFACT_ROOT" && pwd)
# Message to monitor in the log file to determine whether the VM is ready
# It should align with bench_runner.sh
READY_MESSAGE="The VM is ready for the benchmark."

# Import the common functions
BENCHMARK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)/../"
source "${BENCHMARK_ROOT}/common/prepare_host.sh"

if [[ "$BENCHMARK_PATH" =~ "iperf" ]]; then
    # Persist Iperf port
    export IPERF_PORT=5201
elif [[ "$BENCHMARK_PATH" =~ "nginx" ]]; then
    # Persist Nginx port
    export NGINX_PORT=8080
elif [[ "$BENCHMARK_PATH" =~ "redis" ]]; then
    # Persist Redis port
    export REDIS_PORT=6379
elif [[ "$BENCHMARK_PATH" =~ "tcp_virtio_lat" ]]; then
    # Persist lmbench/tcp_lat port
    export LMBENCH_TCP_LAT_PORT=31234
elif [[ "$BENCHMARK_PATH" =~ "tcp_virtio_bw" ]]; then
    # Persist lmbench/bw_tcp port
    export LMBENCH_TCP_BW_PORT=31236
elif [[ "$BENCHMARK_PATH" =~ "memcached" ]]; then
    # Persist Memcached port
    export MEMCACHED_PORT=11211
fi

# Legacy jobs terminate QEMU from host.sh. New jobs can require an orderly
# guest exit by setting runtime_config.check_guest_exit to true.
run_benchmark() {
    local guest_cmd=$1
    local output_file=$2
    local system=$3
    local guest_pid host_status=0 guest_status=0

    export BENCHMARK_SYSTEM="$system"
    export BENCHMARK_ARTIFACT_DIR
    BENCHMARK_ARTIFACT_DIR=$(mktemp -d "$ARTIFACT_ROOT/$system.XXXXXX")
    local guest_log_file="$BENCHMARK_ARTIFACT_DIR/guest.log"
    echo "Benchmark artifacts: $BENCHMARK_ARTIFACT_DIR"
    if [[ "$CHECK_GUEST_EXIT" == true ]]; then
        # Bound build, readiness and shutdown, including a guest panic after
        # a failed benchmark. timeout signals only its own process group.
        timeout --kill-after=10s 20m bash -o pipefail -c "$guest_cmd" >"$guest_log_file" 2>&1 &
    else
        ( eval "$guest_cmd" 2>&1 | tee "$guest_log_file" ) &
    fi
    guest_pid=$!

    echo "Waiting for the ready message: $READY_MESSAGE"
    until grep -q "$READY_MESSAGE" "$guest_log_file" 2>/dev/null; do
        if ! kill -0 "$guest_pid" 2>/dev/null; then
            wait "$guest_pid" || guest_status=$?
            echo "Guest exited before readiness (status $guest_status)" >&2
            return 1
        fi
        sleep 1
    done

    if [[ "$CHECK_GUEST_EXIT" != true ]]; then
        sleep 1
    fi

    export GUEST_SERVER_IP_ADDRESS=10.0.2.15
    bash "$BENCHMARK_PATH/host.sh" 2>&1 | tee "$output_file" || host_status=$?
    cp "$output_file" "$BENCHMARK_ARTIFACT_DIR/host.log"
    if [[ "$CHECK_GUEST_EXIT" == true && "$host_status" -ne 0 ]]; then
        kill -TERM -- "-$guest_pid" 2>/dev/null || true
    fi
    wait "$guest_pid" || guest_status=$?
    if [[ "$CHECK_GUEST_EXIT" == true ]]; then
        cat "$guest_log_file"
    fi
    if [[ "$host_status" -ne 0 ]]; then
        echo "Host benchmark failed (status $host_status)" >&2
        return "$host_status"
    fi
    if [[ "$CHECK_GUEST_EXIT" == true && "$guest_status" -ne 0 ]]; then
        echo "Guest benchmark failed (status $guest_status)" >&2
        return "$guest_status"
    fi
    if [[ "$CHECK_GUEST_EXIT" == true ]] &&
        ! tr -d '\r' <"$guest_log_file" | grep -Fx "BENCHMARK_SUCCEEDED $BENCHMARK_NAME" >/dev/null; then
        echo "Guest benchmark did not report successful completion" >&2
        return 1
    fi
}

run_benchmark "$ASTERINAS_GUEST_CMD" "$ASTERINAS_OUTPUT" asterinas
prepare_fs "$BENCHMARK_NAME"
run_benchmark "$LINUX_GUEST_CMD" "$LINUX_OUTPUT" linux
