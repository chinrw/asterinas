# SPDX-License-Identifier: MPL-2.0

import json
from pathlib import Path
import platform
import struct
import subprocess

from common import capture, path_info, roots, run, write_json

output = Path("/evidence")
rows = json.loads(Path("/expected-roots.json").read_text())
paths = roots(rows)
initial = Path("/tmp/initial-store.txt").read_text().splitlines()
assert initial and not set(initial).intersection(paths)
write_json(output / "initial-store.json", initial)
subprocess.run(["nix-store", "--check-validity", *paths], check=True)
write_json(output / "runtime-closure.json", path_info(paths))
run(["du", "-sk", "/nix/store", "/nix/var/nix/db", "/nix/var/log/nix", "/root/.cache/nix"], output / "final-disk-usage.log")
(output / "final-store.txt").write_text(capture(["nix", "--extra-experimental-features", "nix-command", "path-info", "--all"]))
for index, path in enumerate(paths):
    run(["nix-store", "--verify-path", path], output / f"verify-{index}.log")

machine_ids = {"x86_64_pkgs": 62, "aarch64_pkgs": 183, "riscv64_pkgs": 243}
elf = []
for row in rows:
    if row["attr"] == "busybox":
        binary = Path(row["paths"][0]) / "bin/busybox"
        header = binary.read_bytes()[:20]
        assert header[:4] == b"\x7fELF", binary
        machine = struct.unpack("<H" if header[5] == 1 else ">H", header[18:20])[0]
        assert machine == machine_ids[row["consumer"]], (binary, machine)
        elf.append({"path": str(binary), "elf_machine": machine, "consumer": row["consumer"]})
        native = "x86_64_pkgs" if platform.machine() == "x86_64" else "aarch64_pkgs"
        if row["consumer"] == native:
            run([str(binary), "true"], output / "native-busybox.log")
write_json(output / "guest-elf.json", elf)

host = {row["attr"]: row["paths"][0] for row in rows if row["consumer"] == "install_host_pkgs"}
for name, args in {
    "apacheHttpd": ["ab", "-V"],
    "iperf3": ["iperf3", "--version"],
    "libmemcached": ["memaslap", "--version"],
    "lmbench": ["lat_syscall", "null"],
    "redis": ["redis-cli", "--version"],
}.items():
    run([str(Path(host[name]) / "bin" / args[0]), *args[1:]], output / f"host-{name}.log")

link = Path("/nix/var/nix/gcroots/auto/x86_64-initramfs-with-benchmark")
assert link.exists(), link
metadata = json.loads(capture(["nix", "--extra-experimental-features", "nix-command", "path-info", "--json", str(link.resolve())]))
metadata = metadata[str(link.resolve())] if isinstance(metadata, dict) else metadata[0]
initially_present = str(link.resolve()) in initial
assert not initially_present
write_json(output / "initramfs.json", {"path": str(link.resolve()), "size_bytes": link.stat().st_size, "metadata": metadata, "deriver": capture(["nix-store", "--query", "--deriver", str(link.resolve())]).strip(), "initially_present": initially_present})
write_json(output / "result.json", {"result": "passed", "root_count": len(paths), "guest_architectures": len(elf), "host_architecture": platform.machine()})
