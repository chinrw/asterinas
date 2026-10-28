#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
suite=$(cd "$(dirname "$0")" && pwd)
exec bash "$suite/host_watchdog.sh"
