# SPDX-License-Identifier: MPL-2.0

import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import threading
import time

TARGETS = ("aarch64_pkgs", "riscv64_pkgs", "x86_64_pkgs", "install_host_pkgs")
CACHE = "asterina-test"
CACHE_KEY = "asterina-test.cachix.org-1:LUjQ7OX2Ur+VC605+JQbI1wGyYTHN90RdmICRwc4K8M="


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def capture(command, **kwargs):
    return subprocess.check_output(command, text=True, **kwargs)


def run(command, log, **kwargs):
    start = time.monotonic()
    with Path(log).open("w") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, **kwargs)
    record = {"command": command, "elapsed_s": time.monotonic() - start, "exit_code": result.returncode}
    write_json(str(log) + ".json", record)
    if result.returncode:
        print(Path(log).read_text()[-12000:], flush=True)
        raise subprocess.CalledProcessError(result.returncode, command)
    return record


def inventory(source, output):
    rows = []
    commands = {}
    for target in TARGETS:
        text = capture(["make", "--no-print-directory", "-n", "-C", str(source / "test/initramfs"), target])
        tokens = shlex.split(text.replace("\\\n", " "))
        attrs = [tokens[i + 1] for i, word in enumerate(tokens) if word == "-A"]
        params = {}
        for i, word in enumerate(tokens):
            if word in ("--arg", "--argstr"):
                params[tokens[i + 1]] = json.dumps(tokens[i + 2]) if word == "--argstr" else tokens[i + 2]
        commands[target] = {"command": text, "attributes": attrs, "parameters": params}
        args = " ".join(f"{key} = {value};" for key, value in params.items())
        selected = "d.meta.outputsToInstall or [ d.outputName ]" if target == "install_host_pkgs" else "[ d.outputName ]"
        expression = f"""
          let
            p = import {source}/test/initramfs/nix {{ {args} }};
            lib = (import {source}/distro/nixpkgs.nix {{}}).lib;
          in map (attr:
            let d = lib.getAttrFromPath (lib.splitString "." attr) p;
            in {{ inherit attr; consumer = {json.dumps(target)}; drvPath = d.drvPath;
              paths = map (name: d.${{name}}.outPath) ({selected});
            }}) (builtins.fromJSON {json.dumps(json.dumps(attrs))})
        """
        rows.extend(json.loads(capture(["nix-instantiate", "--eval", "--strict", "--json", "--expr", expression])))
    write_json(output / "commands.json", commands)
    write_json(output / "expected-roots.json", rows)
    return rows


def roots(rows):
    return sorted({path for row in rows for path in row["paths"]})


def path_info(paths):
    value = json.loads(capture(["nix", "--extra-experimental-features", "nix-command", "path-info", "--json", "--recursive", *paths]))
    return value if isinstance(value, dict) else {row["path"]: row for row in value}


def record_machine(output):
    write_json(output / "machine.json", {"architecture": platform.machine(), "kernel": platform.release(), "cpus": os.cpu_count()})
    for name, command in {
        "lscpu": ["lscpu", "--json"],
        "memory": ["free", "-b"],
        "disk": ["df", "-B1"],
        "docker": ["docker", "info", "--format", "{{json .}}"],
        "buildx": ["docker", "buildx", "version"],
    }.items():
        with (output / f"{name}.txt").open("w") as stream:
            subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)


class HostMonitor:
    def __init__(self, path):
        self.path = Path(path)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self):
        start = time.monotonic()
        with self.path.open("w") as stream:
            while True:
                row = {"elapsed_s": time.monotonic() - start, "filesystem_sample_path": str(self.path.parent)}
                for name in ("stat", "diskstats", "meminfo", "loadavg", "net/dev", "net/route", "pressure/cpu", "pressure/io"):
                    path = Path("/proc") / name
                    if path.exists():
                        row[name] = path.read_text()
                fs = os.statvfs(self.path.parent)
                row["filesystem_used_bytes"] = (fs.f_blocks - fs.f_bfree) * fs.f_frsize
                row["filesystem_available_bytes"] = fs.f_bavail * fs.f_frsize
                stream.write(json.dumps(row) + "\n")
                stream.flush()
                if self.stop.wait(5):
                    break

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()
