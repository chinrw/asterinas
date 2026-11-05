#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -eu

exec sh "$(dirname "$0")/../timer_common/run.sh" churn
