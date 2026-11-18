#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

EPOLLTEST_DIR=/test/io/epoll
cd ${EPOLLTEST_DIR}

echo "Start epoll_pwait test......"

EPOLL_PID=""

cleanup() {
    if [ -n "$EPOLL_PID" ]; then
        kill "$EPOLL_PID" 2>/dev/null || true
        wait "$EPOLL_PID" 2>/dev/null || true
    fi
}

trap cleanup EXIT
trap 'exit 1' HUP INT TERM

./epoll_pwait &
EPOLL_PID=$!

echo "epoll_pwait PID: $EPOLL_PID"

# Give epoll_pwait time to initialize and block SIGUSR1.
sleep 1

kill -USR1 $EPOLL_PID
echo "Sent SIGUSR1 to PID $EPOLL_PID"

EPOLL_STATUS=0
wait "$EPOLL_PID" || EPOLL_STATUS=$?
EPOLL_PID=""

echo "Test completed."
exit "$EPOLL_STATUS"
