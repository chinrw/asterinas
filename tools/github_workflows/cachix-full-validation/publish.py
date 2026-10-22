# SPDX-License-Identifier: MPL-2.0

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from common import CACHE, TARGETS, inventory, path_info, record_machine, roots, run, write_json

source = Path.cwd()
output = Path(sys.argv[1]).resolve()
output.mkdir(parents=True, exist_ok=True)
if not os.environ.get("CACHIX_AUTH_TOKEN"):
    raise SystemExit("CACHIX_AUTH_TOKEN_FOR_TEST_CACHE is required")
record_machine(output)
rows = inventory(source, output)
build_env = os.environ.copy()
build_env.pop("CACHIX_AUTH_TOKEN", None)
result_root = Path(os.environ["RUNNER_TEMP"]) / "cachix-prebuilt-roots"
result_root.mkdir()
stages = []
summary = {"stages": stages, "expected_roots": roots(rows), "complete": False}
try:
    for target in TARGETS:
        stage = {"target": target}
        stages.append(stage)
        command = ["make", "--no-print-directory", "-C", "test/initramfs", target, f"PREBUILT_ROOT={result_root}", "VERBOSE=1"]
        start = time.monotonic()
        with (output / f"{target}.paths").open("w") as stdout, (output / f"{target}.build.log").open("w") as stderr:
            subprocess.run(command, stdout=stdout, stderr=stderr, env=build_env, check=True)
        stage["build_s"] = time.monotonic() - start
        selected = sorted({path for row in rows if row["consumer"] == target for path in row["paths"]})
        if target == "install_host_pkgs":
            upload = [str((Path.home() / ".nix-profile").resolve())]
        else:
            upload = (output / f"{target}.paths").read_text().splitlines()
            assert set(upload) == set(selected), (target, upload, selected)
        write_json(output / f"{target}.closure.json", path_info(selected))
        stage["upload"] = run(["cachix", "push", CACHE, *upload], output / f"{target}.upload.log")
        stage["gc"] = run(["make", "-C", "test/initramfs", "nix_gc"], output / f"{target}.gc.log", env=build_env)
        subprocess.run(["nix-store", "--check-validity", *selected], check=True, env=build_env)
        write_json(output / "summary.json", summary)
        print(json.dumps(stage), flush=True)
    write_json(output / "published-closure.json", path_info(roots(rows)))
    summary["complete"] = True
finally:
    write_json(output / "summary.json", summary)
