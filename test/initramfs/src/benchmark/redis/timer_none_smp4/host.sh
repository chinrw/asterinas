#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -eu

exec python3 "$(dirname "$0")/../timer_common/host.py" none 4
