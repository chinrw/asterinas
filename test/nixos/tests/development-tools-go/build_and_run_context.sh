#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
suite=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$suite/../../../.." && pwd)
cd "$repo"
# The unchanged base already reproduced in the preceding paired diagnostic.
make nixos NIXOS_TEST_SUITE=development-tools-go NIXOS_DISK_SIZE_IN_MB=8192
python3 "$suite/minimal_linux_controls.py"
python3 "$suite/timeline_linux_controls.py"
directory="$repo/target/notingo-native-controls/timeline"
cp "$repo/target/osdk/asterinas-run-base/Cargo.lock" "$directory/instrumented-Cargo.lock"
sha256sum "$repo/target/osdk/asterinas/asterinas-osdk-bin" > "$directory/instrumented-kernel-sha256.txt"
bash "$suite/run_timeline_controls.sh"
