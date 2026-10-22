# SPDX-License-Identifier: MPL-2.0

import bz2
import difflib
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys

from common import capture, run, write_json

output = Path("repro-results").resolve()
output.mkdir(exist_ok=True)


def manifest(root):
    result = {}
    for folder, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(folder) / name
            info = path.lstat()
            row = {"mode": stat.S_IMODE(info.st_mode)}
            if path.is_symlink():
                row["link"] = os.readlink(path)
            elif path.is_file():
                row.update(size=info.st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            else:
                row["directory"] = True
            result[str(path.relative_to(root))] = row
    return result


def sections(path):
    data = path.read_bytes()
    if not data.startswith(b"\x7fELF"):
        return None
    text = capture(["readelf", "--wide", "--sections", str(path)])
    result = {}
    for line in text.splitlines():
        match = re.search(r"\[\s*\d+\]\s+(\S+)\s+(\S+)\s+[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)", line)
        if match:
            name, kind, offset, size = match.groups()
            part = data[int(offset, 16):int(offset, 16) + int(size, 16)]
            result[name] = {"type": kind, "size": int(size, 16)}
            if kind != "NOBITS":
                result[name]["sha256"] = hashlib.sha256(part).hexdigest()
    return result


def compare(original, rebuilt, folder):
    left, right = manifest(original), manifest(rebuilt)
    write_json(folder / "cached-manifest.json", left)
    write_json(folder / "rebuilt-manifest.json", right)
    changed = {name: {"cached": left.get(name), "rebuilt": right.get(name)} for name in sorted(set(left) | set(right)) if left.get(name) != right.get(name)}
    write_json(folder / "changed-files.json", changed)
    details = {}
    for index, name in enumerate(list(changed)[:12]):
        a, b = original / name, rebuilt / name
        if not a.is_file() or not b.is_file() or a.is_symlink() or b.is_symlink():
            continue
        sa, sb = sections(a), sections(b)
        if sa is not None and sb is not None:
            details[name] = {"sections": {key: {"cached": sa.get(key), "rebuilt": sb.get(key)} for key in sorted(set(sa) | set(sb)) if sa.get(key) != sb.get(key)}}
        strings_a = capture(["strings", "-a", str(a)]).splitlines()
        strings_b = capture(["strings", "-a", str(b)]).splitlines()
        difference = list(difflib.unified_diff(strings_a, strings_b, fromfile="cached", tofile="rebuilt", n=2))
        (folder / f"strings-{index}.diff").write_text("\n".join(difference[:300]) + "\n")
        details.setdefault(name, {})["strings_diff_lines"] = len(difference)
    write_json(folder / "file-details.json", details)
    return {"changed_files": len(changed), "examples": list(changed)[:12]}


native = "x86_64" if platform.machine() == "x86_64" else "aarch64"
cases = [("busybox-riscv64", "riscv64", "busybox"), ("ltp-native", native, "conformance.ltp"), ("kselftest-x86_64", "x86_64", "conformance.kselftest")]
direct_busybox = "--direct-busybox" in sys.argv
if direct_busybox:
    cases = cases[:1]
summary = {"architecture": platform.machine(), "checks": []}
try:
    for label, target, attr in cases:
        folder = output / label
        folder.mkdir()
        command = ["nix-build", "test/initramfs/nix/default.nix", "--argstr", "target", target, "--arg", "enableBenchmarkTest", "true" if target == "x86_64" else "false", "--arg", "enableConformanceTest", "true", "-A", attr, "--out-link", str(Path(os.environ["RUNNER_TEMP"]) / label)]
        run(command, folder / "seed.log")
        original = Path(os.path.realpath(Path(os.environ["RUNNER_TEMP"]) / label))
        record = {"label": label, "output": str(original), "checks": []}
        summary["checks"].append(record)
        for sandbox in ("true", "false"):
            check_folder = folder / f"sandbox-{sandbox}"
            check_folder.mkdir()
            with (check_folder / "build.log").open("w") as log:
                result = subprocess.run([*command, "--check", "--keep-failed", "--option", "sandbox", sandbox], stdout=log, stderr=subprocess.STDOUT)
            row = {"sandbox": sandbox, "exit_code": result.returncode}
            record["checks"].append(row)
            rebuilt = Path(str(original) + ".check")
            log_text = (check_folder / "build.log").read_text()
            different = result.returncode != 0 and "may not be deterministic" in log_text and rebuilt.exists()
            assert result.returncode == 0 or different, (label, sandbox, result.returncode)
            row["different"] = different
            if different:
                row.update(compare(original, rebuilt, check_folder))
                run(["sudo", "mv", str(rebuilt), str(Path(os.environ["RUNNER_TEMP"]) / f"{label}-sandbox-{sandbox}")], check_folder / "preserve.log")
            write_json(output / "summary.json", summary)
            print(json.dumps({"label": label, **row}), flush=True)
        if direct_busybox:
            direct_folder = folder / "direct-sandbox-false"
            direct_folder.mkdir()
            preserved = Path(os.environ["RUNNER_TEMP"]) / "cached-busybox"
            shutil.copytree(original, preserved, symlinks=True)
            drv = capture(["nix-instantiate", *command[1:command.index("--out-link")]]).strip()
            outputs = capture(["nix-store", "--query", "--outputs", drv]).splitlines()
            Path(os.environ["RUNNER_TEMP"], label).unlink()
            existing = [path for path in outputs if Path(path).exists()]
            run(["nix-store", "--delete", *existing], direct_folder / "delete.log")
            assert not original.exists()
            run([*command, "--option", "sandbox", "false", "--option", "substitute", "false"], direct_folder / "build.log")
            assert Path(os.path.realpath(Path(os.environ["RUNNER_TEMP"]) / label)) == original
            row = compare(preserved, original, direct_folder)
            record["direct_sandbox_false"] = row
            for variant, root in (("cached", preserved), ("rebuilt", original)):
                binary = root / "bin/busybox"
                data = binary.read_bytes()
                shutil.copy2(binary, direct_folder / f"{variant}-busybox")
                blocks = []
                for index, match in enumerate(re.finditer(b"h[1-9]1AY&SY", data)):
                    decoder = bz2.BZ2Decompressor()
                    try:
                        decoded = decoder.decompress(b"BZ" + data[match.start():])
                    except (OSError, EOFError):
                        continue
                    name = f"{variant}-bzip2-{index}.bin"
                    (direct_folder / name).write_bytes(decoded)
                    blocks.append({"offset": match.start(), "decoded_bytes": len(decoded), "file": name})
                write_json(direct_folder / f"{variant}-compressed-blocks.json", blocks)
            write_json(output / "summary.json", summary)
finally:
    write_json(output / "summary.json", summary)
