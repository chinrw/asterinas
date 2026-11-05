#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -eu

condition=$1
redis_cli=/benchmark/bin/redis-cli
redis_server=/benchmark/bin/redis-server
port=${REDIS_PORT:-6379}
guest_address=${REDIS_GUEST_ADDRESS:-10.0.2.15}
work_dir=$(mktemp -d /tmp/redis-timer.XXXXXX)
server_pid=
churn_pid=

redis() {
    timeout 5 "$redis_cli" -e --raw -h "$guest_address" -p "$port" "$@"
}

# Waits occur only during setup and teardown, outside the measured interval.
wait_stopped() {
    owned_pid=$1
    attempts=0
    while kill -0 "$owned_pid" 2>/dev/null; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 100 ]; then
            kill -KILL "$owned_pid" 2>/dev/null || true
            return 1
        fi
        sleep 0.1
    done
}

cleanup() {
    exit_status=$?
    trap - EXIT HUP INT TERM
    if [ -n "$churn_pid" ]; then
        kill -TERM "$churn_pid" 2>/dev/null || true
        wait_stopped "$churn_pid" || exit_status=1
        wait "$churn_pid" || exit_status=1
    fi
    if [ -n "$server_pid" ]; then
        redis SHUTDOWN NOSAVE >/dev/null 2>&1 || true
        kill -TERM "$server_pid" 2>/dev/null || true
        wait_stopped "$server_pid" || exit_status=1
        wait "$server_pid" || exit_status=1
    fi
    echo "Redis timer guest logs: $work_dir"
    cat "$work_dir/server.log" 2>/dev/null || true
    cat "$work_dir/churn.log" 2>/dev/null || true
    exit "$exit_status"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

case "$condition" in
    none|churn) ;;
    *) echo "Invalid timer condition: $condition" >&2; exit 1 ;;
esac

"$redis_server" --bind "$guest_address" --port "$port" --protected-mode no \
    --save '' --appendonly no --daemonize no --dir "$work_dir" \
    >"$work_dir/server.log" 2>&1 &
server_pid=$!
attempts=0
until [ "$(redis PING 2>/dev/null || true)" = PONG ]; do
    kill -0 "$server_pid"
    attempts=$((attempts + 1))
    [ "$attempts" -lt 100 ] || exit 1
    sleep 0.1
done

redis SET timer-bench:condition "$condition" >/dev/null
redis SET fixed-perf-key abc >/dev/null
# redis-benchmark GET uses this key when random key selection is disabled.
redis SET 'key:__rand_int__' abc >/dev/null

if [ "$condition" = churn ]; then
    /benchmark/bin/timerfd-bench churn 5000 120 >"$work_dir/churn.log" 2>&1 &
    churn_pid=$!
    attempts=0
    until grep -q '^TIMERFD_CHURN_STARTED ' "$work_dir/churn.log"; do
        kill -0 "$churn_pid"
        attempts=$((attempts + 1))
        [ "$attempts" -lt 100 ] || exit 1
        sleep 0.1
    done
    kill -0 "$churn_pid"
    redis SET timer-bench:load-start "$(grep '^TIMERFD_CHURN_STARTED ' "$work_dir/churn.log")" >/dev/null
fi

redis SET timer-bench:ready 1 >/dev/null
done_message=$(timeout 125 "$redis_cli" -e --raw -h "$guest_address" -p "$port" \
    BLPOP timer-bench:done 120)
case "$done_message" in
    'timer-bench:done
1') ;;
    *) echo "Missing host completion message" >&2; exit 1 ;;
esac

load_status=0
if [ -n "$churn_pid" ]; then
    # A helper that exited before the done message cannot prove coverage.
    kill -0 "$churn_pid" 2>/dev/null || load_status=1
    kill -TERM "$churn_pid" 2>/dev/null || load_status=1
    wait_stopped "$churn_pid" || load_status=1
    wait "$churn_pid" || load_status=1
    churn_pid=
    redis SET timer-bench:load-log "$(cat "$work_dir/churn.log")" >/dev/null
fi
redis SET timer-bench:load-exit "$load_status" >/dev/null
redis SET timer-bench:status complete >/dev/null
redis RPUSH timer-bench:completed 1 >/dev/null

ack_message=$(timeout 25 "$redis_cli" -e --raw -h "$guest_address" -p "$port" \
    BLPOP timer-bench:ack 20)
case "$ack_message" in
    'timer-bench:ack
1') ;;
    *) echo "Missing host acknowledgement" >&2; exit 1 ;;
esac

redis SHUTDOWN NOSAVE
server_status=0
wait_stopped "$server_pid" || server_status=1
wait "$server_pid" || server_status=1
server_pid=
echo "REDIS_TIMER_SERVER_EXIT $server_status"
[ "$load_status" -eq 0 ] && [ "$server_status" -eq 0 ]
