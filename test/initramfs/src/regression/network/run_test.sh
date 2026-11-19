#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

. "${REGRESSION_TEST_ROOT:-/test}/run_test_case.sh"

run_client_server() {
    "$1" &
    sleep 0.2
    "$2"
}

run_test_case run_client_server ./tcp_server ./tcp_client
run_test_case run_client_server ./udp_server ./udp_client
run_test_case run_client_server ./unix_server ./unix_client

run_test_case ./broadcast_complex
run_test_case ./listen_backlog
run_test_case ./msg_peek
run_test_case ./msg_trunc
run_test_case ./privileged_ports
run_test_case ./send_buf_full
run_test_case ./sendmmsg
run_test_case ./socketpair
run_test_case_with_linux_module vsock ./socket_ioctl
run_test_case ./sockoption
run_test_case ./sockoption_short
run_test_case ./sockoption_unix
run_test_case ./tcp_accept_reset
run_test_case ./tcp_err
run_test_case ./tcp_poll
run_test_case ./tcp_reuseaddr
if [ "${REGRESSION_TEST_PLATFORM:-asterinas}" = linux ]; then
    skip_test_case "This tests Asterinas's ring-buffered TCP implementation." ./tcp_wrapped_buffer_io
else
    run_test_case ./tcp_wrapped_buffer_io
fi
run_test_case ./udp_broadcast
run_test_case ./udp_err
run_test_case ./unix_datagram_err
run_test_case ./unix_seqpacket_err
run_test_case ./unix_stream_err

run_test_case ./netlink_padding
run_test_case ./netlink_route
run_test_case ./netlink_sockoption
run_test_case ./rtnl_err
run_test_case ./uevent_err

finish_test_cases
