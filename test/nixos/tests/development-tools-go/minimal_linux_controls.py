# SPDX-License-Identifier: MPL-2.0
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

suite = Path(__file__).resolve().parent
repo = suite.parents[3]
artifacts = repo / "target/notingo-native-controls"
artifacts.mkdir(parents=True, exist_ok=True)
if "--check" in sys.argv:
    rows = json.loads((artifacts / "linux-minimal-results.json").read_text())["controls"]
    sys.exit(0 if len(rows) == 20 and all(row["passed"] for row in rows) else 1)
expression = f"import {suite}/minimal_helper.nix {{ pkgs = import {repo}/distro/nixpkgs.nix {{}}; }}"
output = subprocess.check_output(["nix-build", "--no-out-link", "--expr", expression], cwd=repo, text=True).strip().splitlines()[-1]
helper = Path(output) / "bin/minimal-cgo"
checksum = hashlib.sha256(helper.read_bytes()).hexdigest()
expected = json.loads((suite / "minimal_layout.json").read_text())["sha256"]
assert checksum == expected, (checksum, expected)
shutil.copy2(helper, artifacts / "minimal-helper")
shutil.copy2(suite / "GO-LICENSE", artifacts / "GO-LICENSE")
(artifacts / "minimal-sha256.txt").write_text(checksum + "\n")
os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
environment = dict(os.environ, GOMAXPROCS="1")
for key in ("GODEBUG", "GOTRACEBACK"):
    environment.pop(key, None)
rows = []
for mode in ("poll", "yield"):
    for iteration in range(1, 11):
        start = time.monotonic()
        run = subprocess.run(["timeout", "--signal=KILL", "10s", str(helper), mode], env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        row = {"mode": mode, "iteration": iteration, "seconds": time.monotonic() - start, "exit_code": run.returncode, "passed": run.returncode == 0 and run.stdout.strip() == "BARRIER_OK"}
        rows.append(row)
        (artifacts / f"linux-minimal-{mode}-{iteration}.log").write_text(run.stdout)
        print("LINUX_MINIMAL_RESULT " + json.dumps(row), flush=True)
        if not row["passed"]:
            break
(artifacts / "linux-minimal-results.json").write_text(json.dumps({"sha256": checksum, "controls": rows}, indent=2) + "\n")
