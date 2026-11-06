#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Build the frozen base crate with the actual command exported by OSDK."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--jobs", type=int, default=8)
args = parser.parse_args()
root = Path(__file__).resolve().parents[3]
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
env = os.environ | {
    "CARGO_TARGET_DIR": str(output / "target"),
    "CARGO_BUILD_JOBS": str(args.jobs),
    "OSTD_TASK_STACK_SIZE_IN_PAGES": "64",
    "OSDK_BUILD_USERNAME": "crate2nix-ci",
    "OSDK_BUILD_HOSTNAME": "crate2nix-ci",
}
toolchain = subprocess.check_output(["rustc", "--print", "sysroot"], text=True, env=env).strip()
env["RUSTFLAGS"] = "--remap-path-prefix=" + toolchain + "=/rustc"
description_file = output / "build-description.json"
subprocess.run(
    [str(root / "target/c2n-exp-prepare/release/cargo-osdk"), "osdk", "build",
     "--boot-method=vmm-direct", "--grub-boot-protocol=multiboot2",
     "--output", str(output / "export"), "--export-build-description", str(description_file)],
    cwd=root / "kernel", env=env, check=True,
)
description = json.loads(description_file.read_text())
base = Path(description["base_crate"])
shutil.copyfile(root / "tools/experiments/crate2nix/generated/base/Cargo.lock", base / "Cargo.lock")


def os_string(value):
    return bytes(value["Unix"]).decode() if isinstance(value, dict) else value


for key, value in description["env"]:
    key = os_string(key)
    if value is None:
        env.pop(key, None)
    else:
        env[key] = os_string(value)
env.pop("RUSTC_WRAPPER", None)
env.pop("RUSTC_WORKSPACE_WRAPPER", None)
command = [os_string(description["program"])] + [os_string(arg) for arg in description["args"]] + ["--locked", "-vv"]
started = time.monotonic()
with (output / "build.log").open("w") as log:
    result = subprocess.run(command, cwd=base, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
elapsed = time.monotonic() - started
metrics = {
    "scenario": "cargo-baseline",
    "environment": "github-runner-fresh-target" if os.environ.get("GITHUB_ACTIONS") else "local-fresh-target",
    "realization_seconds": elapsed, "exit_code": result.returncode,
    "validation_passed": result.returncode == 0,
    "valid_performance_sample": result.returncode == 0,
    "command": command, "cargo_jobs": args.jobs,
    "kernel": str(output / "target/x86_64-unknown-none/debug/asterinas-osdk-bin"),
}
(output / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
print(json.dumps(metrics, indent=2))
raise SystemExit(result.returncode)
