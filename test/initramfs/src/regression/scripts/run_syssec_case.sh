#!/bin/sh

# SPDX-License-Identifier: MPL-2.0

set -eu

case_path=$1

if [ ! -x "$case_path" ]; then
    echo "Syssec case is not executable: $case_path" >&2
    exit 126
fi

exec "$case_path"
