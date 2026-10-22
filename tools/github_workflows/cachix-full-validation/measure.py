# SPDX-License-Identifier: MPL-2.0

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

command = sys.argv[1:]
profile = os.environ.get("PROFILE_STORE") == "1"
samples = []
stop = threading.Event()


def counters():
    result = {}
    for name in ("cpu.stat", "io.stat", "memory.peak", "memory.events"):
        path = Path("/sys/fs/cgroup") / name
        if path.exists():
            result[name] = path.read_text()
    return result


def sample():
    start = time.monotonic()
    result = subprocess.run(["du", "-sk", "/nix", "/tmp"], capture_output=True, text=True)
    row = {"scan_s": time.monotonic() - start, "at_monotonic_s": time.monotonic()}
    for line in result.stdout.splitlines():
        size, path = line.split(maxsplit=1)
        row[path] = int(size) * 1024
    samples.append(row)


def monitor():
    while not stop.wait(20):
        sample()


if command == ["--snapshot"]:
    sample()
    print("STORE_SNAPSHOT " + json.dumps(samples[0]), flush=True)
    raise SystemExit(0)

if profile:
    sample()
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
before = counters()
start = time.monotonic()
result = subprocess.run(command)
elapsed = time.monotonic() - start
after = counters()
if profile:
    stop.set()
    thread.join()
    sample()
record = {"command": command, "elapsed_s": elapsed, "exit_code": result.returncode, "profile_store": profile, "cgroup_before": before, "cgroup_after": after}
if samples:
    record.update(samples=samples, scan_seconds=sum(row["scan_s"] for row in samples), peak_nix_tmp_bytes=max(row.get("/nix", 0) + row.get("/tmp", 0) for row in samples))
print("MEASUREMENT " + json.dumps(record), flush=True)
sys.exit(result.returncode)
