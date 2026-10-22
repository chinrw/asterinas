# SPDX-License-Identifier: MPL-2.0

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=["baseline", "candidate"])
parser.add_argument("--source", type=Path, default=Path.cwd())
parser.add_argument("--output", type=Path, default=Path("cachix-results"))
args = parser.parse_args()
source = args.source.resolve()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
context = output / "context"
(context / "test").mkdir(parents=True, exist_ok=True)
(context / "distro").mkdir(exist_ok=True)
shutil.copytree(source / "test/initramfs", context / "test/initramfs", dirs_exist_ok=True)
shutil.copy2(source / "distro/nixpkgs.nix", context / "distro/nixpkgs.nix")
shutil.copy2(source / ".dockerignore", context / ".dockerignore")
shutil.copy2(Path(__file__).with_name("measure.py"), context / "measure.py")
dockerfile = (source / "tools/dev_env/docker/prebuilt-nix-packages/Dockerfile").read_text()
bootstrap, consumer = dockerfile.split("#= Build Nix packages", 1)
if args.mode == "baseline":
    start = bootstrap.index("    && printf '%s\\n'")
    end = bootstrap.index("    && rm /nix/var/nix/gcroots", start)
    bootstrap = bootstrap[:start] + bootstrap[end:]
(context / "bootstrap.Dockerfile").write_text(bootstrap)
tag = f"asterinas-cachix-validation:{args.mode}-{platform.machine()}"
base_tag = tag + "-bootstrap"
consumer = consumer[consumer.index("COPY test/initramfs"):].replace("VERBOSE=0", "VERBOSE=1")
header = f"FROM {base_tag}\n" + """RUN printf '%s\\n' 'sandbox = false' 'max-jobs = 2' 'cores = 4' 'connect-timeout = 30' 'stalled-download-timeout = 120' >> /etc/nix/nix.conf
COPY measure.py /tmp/cache-measure.py
SHELL ["/usr/bin/python3", "/tmp/cache-measure.py", "/tmp/measurement.json", "/bin/bash", "-c"]
"""
(context / "Dockerfile").write_text(header + consumer)
summary = {"mode": args.mode, "architecture": platform.machine()}
version = (source / "DOCKER_IMAGE_VERSION").read_text().strip()
for stage, command in [
    ("bootstrap", ["docker", "build", "--network=host", "--progress=plain", "--build-arg", f"BASE_VERSION={version}", "-t", base_tag, "-f", str(context / "bootstrap.Dockerfile"), str(context)]),
    ("consumer", ["docker", "build", "--network=host", "--progress=plain", "-t", tag, str(context)]),
]:
    start = time.monotonic()
    with (output / f"{stage}.log").open("w") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    summary[stage] = {"elapsed_s": time.monotonic() - start, "exit_code": result.returncode}
    if stage == "consumer":
        summary["measurements"] = [json.loads(line.split("MEASUREMENT ", 1)[1]) for line in (output / "consumer.log").read_text().splitlines() if "MEASUREMENT {" in line]
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if result.returncode:
        raise SystemExit(result.returncode)
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", base_tag if stage == "bootstrap" else tag], text=True))[0]
    summary[stage].update(image_id=image["Id"], image_size_bytes=image["Size"])
(output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
