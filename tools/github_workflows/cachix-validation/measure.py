# SPDX-License-Identifier: MPL-2.0

import datetime
import json
from pathlib import Path
import subprocess
import sys
import time

destination = Path(sys.argv[1])
command = sys.argv[2:]
samples = []


def sample():
    result = subprocess.run(["du", "-sk", "/nix", "/tmp"], capture_output=True, text=True)
    sizes = {}
    for line in result.stdout.splitlines():
        value, path = line.split(maxsplit=1)
        sizes[path] = int(value) * 1024
    samples.append({"elapsed_s": round(time.monotonic() - start, 3), **sizes})


started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
start = time.monotonic()
sample()
process = subprocess.Popen(command)
while process.poll() is None:
    sample()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
elapsed = time.monotonic() - start
sample()
result = {
    "command": command,
    "started_at": started_at,
    "elapsed_s": elapsed,
    "exit_code": process.returncode,
    "sample_interval_s": 2,
    "initial_bytes": samples[0],
    "final_bytes": samples[-1],
    "peak_nix_bytes": max(s.get("/nix", 0) for s in samples),
    "peak_nix_tmp_bytes": max(s.get("/nix", 0) + s.get("/tmp", 0) for s in samples),
    "samples": samples,
}
destination.write_text(json.dumps(result, indent=2) + "\n")
print("MEASUREMENT " + json.dumps({k: v for k, v in result.items() if k != "samples"}), flush=True)
sys.exit(process.returncode)
