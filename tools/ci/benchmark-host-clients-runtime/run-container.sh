#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail

source_dir=$(readlink -f "${1:?Pass the clean candidate source directory}")
harness_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
result_dir=$(readlink -m "${2:-$harness_dir/results}")
snapshot_dir="$result_dir/harness"
validation_image=${VALIDATION_IMAGE:-asterinas/dev:0.19.0-20261012}
validation_platform=${VALIDATION_PLATFORM:-linux/amd64}
expected_commit=7615ba74baaa03b9c04b2921d44a7b81977d5606
container_name="asterinas-host-clients-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$result_dir"
mkdir -p "$snapshot_dir"
cp "$harness_dir/run-container.sh" "$harness_dir/in-container.sh" "$harness_dir/functional.py" "$snapshot_dir/"
source_hashes() {
    python3 - "$source_dir" <<'PY'
import hashlib
import os
from pathlib import Path
import subprocess
import sys
root = Path(sys.argv[1])
paths = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z']).split(b'\0')
for raw in paths:
    if not raw:
        continue
    relative = os.fsdecode(raw)
    path = root / relative
    data = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
    print(hashlib.sha256(data).hexdigest(), relative)
PY
}
test "$(git -C "$source_dir" rev-parse HEAD)" = "$expected_commit"
test -z "$(git -C "$source_dir" status --porcelain)"
git -C "$source_dir" rev-parse HEAD > "$result_dir/source-commit.txt"
git -C "$source_dir" rev-parse 26cb04660 > "$result_dir/source-base.txt"
git -C "$source_dir" status --porcelain > "$result_dir/source-status-before.txt"
source_hashes > "$result_dir/source-sha256-before.txt"
sha256sum "$snapshot_dir/run-container.sh" "$snapshot_dir/in-container.sh" "$snapshot_dir/functional.py" > "$result_dir/harness-sha256.txt"
docker image inspect "$validation_image" > "$result_dir/image-inspect.json"
docker version > "$result_dir/docker-version.txt"
uname -a > "$result_dir/host-uname.txt"
date -u +%FT%TZ > "$result_dir/started-at.txt"
cleanup() {
    local rc=$?
    docker inspect "$container_name" > "$result_dir/container-inspect-final.json" 2>&1 || true
    docker rm -f -v "$container_name" > "$result_dir/cleanup.log" 2>&1 || true
    git -C "$source_dir" status --porcelain > "$result_dir/source-status-after.txt"
    source_hashes > "$result_dir/source-sha256-after.txt"
    cmp "$result_dir/source-sha256-before.txt" "$result_dir/source-sha256-after.txt" > "$result_dir/source-unchanged.log" 2>&1 || rc=1
    date -u +%FT%TZ > "$result_dir/finished-at.txt"
    printf '%s\n' "$rc" > "$result_dir/driver-exit-code.txt"
    exit "$rc"
}
trap cleanup EXIT
docker_command=(docker create --name "$container_name" --platform "$validation_platform"
    --network bridge --cpus 4 --memory 8g
    --mount "type=bind,src=$source_dir,dst=/source,readonly"
    --mount "type=bind,src=$snapshot_dir,dst=/harness,readonly"
    --mount "type=bind,src=$result_dir,dst=/evidence"
    --env "VALIDATION_EXPECTED_ARCH=${VALIDATION_EXPECTED_ARCH:-x86_64}"
    --workdir /source "$validation_image" bash /harness/in-container.sh)
printf '%q ' "${docker_command[@]}" > "$result_dir/docker-create-command.txt"
printf '\n' >> "$result_dir/docker-create-command.txt"
"${docker_command[@]}" > "$result_dir/container-id.txt"
docker inspect "$container_name" > "$result_dir/container-inspect-start.json"
set +e
docker start --attach "$container_name" 2>&1 | tee "$result_dir/console.log"
start_status=${PIPESTATUS[0]}
set -e
container_status=$(docker inspect --format '{{.State.ExitCode}}' "$container_name")
printf '%s\n' "$container_status" > "$result_dir/container-exit-code.txt"
test "$start_status" -eq 0
exit "$container_status"
