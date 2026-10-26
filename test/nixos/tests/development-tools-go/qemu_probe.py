#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
import os
from pathlib import Path
import sys

repo = Path(__file__).resolve().parents[4]
variant = os.environ.get("PROBE_CPU_VARIANT", "default")
assert variant in ("default", "periodic")
directory = repo / "target/notingo-native-controls" / ("live-" + variant)
directory.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]
if variant == "periodic":
    index = args.index("-cpu") + 1
    args[index] += ",-tsc-deadline"
args += ["-qmp", "unix:" + str(directory / "qmp.sock") + ",server=on,wait=off"]
with (directory / "qemu-host.log").open("wb") as output:
    os.dup2(output.fileno(), 2)
os.execvp("qemu-system-x86_64", ["qemu-system-x86_64"] + args)
