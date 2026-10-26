# SPDX-License-Identifier: MPL-2.0

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

suite = Path(__file__).resolve().parent
repo = suite.parents[3]
artifacts = repo / "target/notingo-native-controls"
artifacts.mkdir(parents=True, exist_ok=True)
if "--check" in sys.argv:
    result = json.loads((artifacts / "linux-results.json").read_text())
    sys.exit(0 if result["clock_exit_code"] == 0 and all(row["completed"] for row in result["controls"]) else 1)

expression = (
    f"let pkgs = import {repo}/distro/nixpkgs.nix {{ }}; "
    f"in import {suite}/shared_helpers.nix {{ inherit pkgs; }}"
)
output = subprocess.check_output(
    ["nix-build", "--no-out-link", "--expr", expression], cwd=repo, text=True
).strip().splitlines()[-1]
helpers = Path(output)
original = helpers / "bin/original"
checksum = hashlib.sha256(original.read_bytes()).hexdigest()
(artifacts / "sha256.txt").write_text(checksum + "\n")
shutil.copy2(original, artifacts / "shared-helper")
shutil.copy2(helpers / "share/licenses/go/LICENSE", artifacts / "GO-LICENSE")
print(f"LINUX_SHARED_HELPER {original} sha256={checksum}", flush=True)
os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
environment = dict(os.environ, GOMAXPROCS="1")
for key in ("GODEBUG", "GOTRACEBACK"):
    environment.pop(key, None)
clock = subprocess.run(
    ["timeout", "--kill-after=2s", "15s", str(helpers / "bin/clock-probe")],
    env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
print("LINUX_" + clock.stdout.strip(), flush=True)
rows = []
for procs in (1, 2):
    for iteration in range(1, 11):
        started = time.monotonic()
        run = subprocess.run(
            ["timeout", "--signal=QUIT", "--kill-after=2s", "10s", str(original), "NotInGoMetricCgoCallAndCallback"],
            env=dict(environment, GOMAXPROCS=str(procs)),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        elapsed = time.monotonic() - started
        completed = run.returncode == 0 and re.search(r"(?m)^OK\s*$", run.stdout) is not None
        row = {"procs": procs, "iteration": iteration, "seconds": elapsed,
               "exit_code": run.returncode, "completed": completed}
        rows.append(row)
        (artifacts / f"linux-p{procs}-{iteration}.log").write_text(run.stdout)
        print("LINUX_PROBE_RESULT " + json.dumps(row), flush=True)
        if not completed:
            break
(artifacts / "linux-results.json").write_text(json.dumps({"sha256": checksum, "clock_exit_code": clock.returncode, "clock_output": clock.stdout, "controls": rows}, indent=2) + "\n")
