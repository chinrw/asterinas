# SPDX-License-Identifier: MPL-2.0

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

SCAN_PATHS = ("/nix", "/tmp")
SCAN_INTERVAL_S = 20


def summarize_scans(samples):
    """Separate validated traversal totals from legacy observations and errors."""
    valid_totals = []
    legacy_totals = []
    invalid = unknown = 0
    for row in samples:
        complete = all(type(row.get(path)) is int and row[path] >= 0 for path in SCAN_PATHS)
        if "valid" not in row:
            unknown += 1
            if complete:
                legacy_totals.append(sum(row[path] for path in SCAN_PATHS))
        elif row["valid"] and row.get("exit_code") == 0 and complete:
            valid_totals.append(sum(row[path] for path in SCAN_PATHS))
        else:
            invalid += 1
    status = "invalid" if invalid else "unknown" if unknown else "valid" if valid_totals else "unavailable"
    return {
        "status": status,
        "valid_samples": len(valid_totals), "invalid_samples": invalid, "unknown_samples": unknown,
        "max_observed_nix_tmp_bytes": max(valid_totals, default=None),
        "legacy_max_observed_nix_tmp_bytes": max(legacy_totals, default=None),
    }


def counters():
    result = {}
    for name in ("cpu.stat", "io.stat", "memory.peak", "memory.events"):
        path = Path("/sys/fs/cgroup") / name
        if path.exists():
            result[name] = path.read_text()
    return result


def sample():
    row = {"scan_start_monotonic_s": time.monotonic(), "exit_code": None, "valid": False}
    try:
        result = subprocess.run(["du", "-sk", *SCAN_PATHS], capture_output=True, text=True)
        row.update(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
        for line in result.stdout.splitlines():
            size, path = line.split(maxsplit=1)
            row[path] = int(size) * 1024
        row["valid"] = result.returncode == 0 and all(path in row and row[path] >= 0 for path in SCAN_PATHS)
    except (OSError, ValueError) as error:
        row["error"] = str(error)
    row["scan_end_monotonic_s"] = time.monotonic()
    row["scan_s"] = row["scan_end_monotonic_s"] - row["scan_start_monotonic_s"]
    return row


def monitor(stop, samples, errors):
    try:
        while not stop.wait(SCAN_INTERVAL_S):
            samples.append(sample())
    except Exception as error:
        # A failed sampler must invalidate the profile in the main thread.
        errors.append(f"{type(error).__name__}: {error}")


def main(command):
    if command == ["--snapshot"]:
        row = sample()
        print("STORE_SNAPSHOT " + json.dumps(row), flush=True)
        return 0 if row["valid"] else 1

    profile = os.environ.get("PROFILE_STORE") == "1"
    samples = []
    sampler_errors = []
    stop = threading.Event()
    if profile:
        samples.append(sample())
        thread = threading.Thread(target=monitor, args=(stop, samples, sampler_errors), daemon=True)
        thread.start()
    try:
        before = counters()
        start = time.monotonic()
        result = subprocess.run(command)
        elapsed = time.monotonic() - start
        after = counters()
    finally:
        if profile:
            stop.set()
            thread.join()
            samples.append(sample())
    record = {
        "command": command, "elapsed_s": elapsed, "exit_code": result.returncode,
        "profile_store": profile, "cgroup_before": before, "cgroup_after": after,
    }
    if profile:
        disk = summarize_scans(samples)
        record.update(
            samples=samples, scan_seconds=sum(row["scan_s"] for row in samples),
            disk_sampling=disk, sampler_errors=sampler_errors,
            profile_valid=disk["status"] == "valid" and not sampler_errors,
        )
    print("MEASUREMENT " + json.dumps(record), flush=True)
    return result.returncode or (1 if profile and not record["profile_valid"] else 0)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
