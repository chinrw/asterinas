#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -e

. "${REGRESSION_TEST_ROOT:-/test}/run_test_case.sh"

if [ -e /dev/tdx_guest ]; then
    # FIXME: Re-enable it once remote attestation works on the self-hosted runner.
    # ./gen_quote/gen_quote
    run_test_case ./tsm_mr/tsm_mr.sh
else
    skip_test_case "No TDX guest device is present." ./tsm_mr/tsm_mr.sh
fi

finish_test_cases
