# SPDX-License-Identifier: MPL-2.0
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

suite = Path(__file__).resolve().parent
repo = suite.parents[3]
artifacts = repo / "target/notingo-native-controls"
artifacts.mkdir(parents=True, exist_ok=True)
os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
environment = dict(os.environ, GOMAXPROCS="1")
for key in ("GODEBUG", "GOTRACEBACK"):
    environment.pop(key, None)
rows = []
digests = []
for name in ("sleep20", "sleep1000"):
    expression = f"(import {suite}/foreign_wait_helpers.nix {{ pkgs = import {repo}/distro/nixpkgs.nix {{}}; }}).{name}"
    output = subprocess.check_output(["nix-build", "--no-out-link", "--expr", expression], cwd=repo, text=True).strip().splitlines()[-1]
    folder = Path(output)
    binary = folder / "bin/minimal-cgo"
    checksum = hashlib.sha256(binary.read_bytes()).hexdigest()
    digests.append(name + " " + checksum)
    shutil.copy2(binary, artifacts / (name + "-helper"))
    shutil.copy2(folder / "runtime-source-sha256.txt", artifacts / (name + "-runtime-source-sha256.txt"))
    for iteration in range(1, 5):
        start = time.monotonic()
        run = subprocess.run(["timeout", "--signal=KILL", "10s", str(binary), "poll"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        row = {"variant": name, "iteration": iteration, "sha256": checksum, "seconds": time.monotonic() - start, "exit_code": run.returncode, "passed": run.returncode == 0 and run.stdout.strip() == "BARRIER_OK"}
        rows.append(row)
        (artifacts / f"linux-{name}-{iteration}.log").write_text(run.stdout)
        print("LINUX_WAIT_VARIANT " + json.dumps(row), flush=True)
(artifacts / "foreign-wait-digests.txt").write_text("\n".join(digests) + "\n")
(artifacts / "linux-wait-variant-results.json").write_text(json.dumps({"controls": rows}, indent=2) + "\n")
assert len(rows) == 8 and all(row["passed"] for row in rows)
