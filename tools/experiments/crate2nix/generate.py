#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Regenerate experimental inputs, or check them without replacing tracked files."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[3]
generated = root / "tools/experiments/crate2nix/generated"
os.chdir(root)
stale = []


def command(argv, **kwargs):
    return subprocess.check_output(argv, text=True, **kwargs)


def output(path, content):
    if path.exists() and path.read_text() == content:
        return
    stale.append(str(path.relative_to(root)))
    if not args.check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def json_output(path, value):
    output(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


generator = command(["nix", "build", ".#c2n-exp-generator", "--no-link", "--print-out-paths"]).strip()
toolchain = command(["nix", "build", ".#c2n-exp-toolchain", "--no-link", "--print-out-paths"]).strip()
env = os.environ | {"PATH": toolchain + "/bin:" + os.environ["PATH"], "OSDK_LOCAL_DEV": "1", "RUSTFLAGS": ""}
output(generated / "rust-sysroot.lock", (Path(toolchain) / "lib/rustlib/src/rust/library/Cargo.lock").read_text())
prepare_target = root / "target/c2n-exp-prepare"
subprocess.run(
    ["cargo", "build", "--release", "--locked", "--manifest-path", "osdk/Cargo.toml",
     "--target-dir", str(prepare_target), "-j", "8"], env=env, check=True,
)

with tempfile.TemporaryDirectory(prefix="asterinas-c2n-generation-") as temporary:
    temporary = Path(temporary)
    description_path = temporary / "build-description.json"
    subprocess.run(
        [str(prepare_target / "release/cargo-osdk"), "osdk", "build",
         "--boot-method=vmm-direct", "--grub-boot-protocol=multiboot2",
         "--output", str(temporary / "export"), "--export-build-description", str(description_path)],
        cwd=root / "kernel", env=env, check=True,
    )
    description = json.loads(description_path.read_text())
    def os_string(value):
        return bytes(value["Unix"]).decode() if isinstance(value, dict) else value

    command_env = {os_string(key): os_string(value) if value is not None else None
                   for key, value in description["env"]}
    tokens = iter(shlex.split(command_env["RUSTFLAGS"]))
    rustflags = []
    for token in tokens:
        rustflags.append(token + " " + next(tokens) if token in ["-C", "-Z", "--cfg", "--check-cfg"] else token)
    link_flags = [flag for flag in rustflags if flag.startswith("-C link-arg=")]
    command_args = [os_string(arg) for arg in description["args"]]
    json_output(generated / "build-contract.json", {
        "target_arch": description["config"]["target_arch"],
        "profile": description["config"]["run"]["build"]["profile"],
        "library_rustflags": [flag for flag in rustflags if flag not in link_flags],
        "final_link_flags": link_flags,
        "build_std_args": [arg for arg in command_args if arg.startswith("-Zbuild-std")],
        "rustc_version": command_env["OSDK_BUILD_RUSTC"],
    })
    source = Path(description["base_crate"])
    for name in ["Cargo.toml", "src/main.rs", "x86_64.ld", "riscv64.ld", "loongarch64.ld", "aarch64.ld"]:
        text = (source / name).read_text()
        if name == "src/main.rs":
            text = "// SPDX-License-Identifier: MPL-2.0\n\n" + text
        if name == "Cargo.toml":
            text = re.sub(
                r'path = "([^"]+)"',
                lambda match: 'path = "' + os.path.relpath(Path(match[1]).resolve(), generated / "base") + '"',
                text,
            )
        output(generated / "base" / name, text)

    # The base crate is a separate Cargo workspace. Its committed lock file
    # freezes the dependency set actually used by the Cargo comparison.
    metadata = json.loads(command(["cargo", "metadata", "--locked", "--format-version=1"], env=env))
    kernel_metadata = json.loads(command(
        ["cargo", "metadata", "--locked", "--format-version=1", "--manifest-path",
         "tools/experiments/crate2nix/generated/base/Cargo.toml"], env=env,
    ))
    members = {
        package["name"]: str(Path(package["manifest_path"]).parent.relative_to(root))
        for package in metadata["packages"] if package["source"] is None
    }
    members["asterinas-osdk-bin"] = "tools/experiments/crate2nix/generated/base"
    json_output(generated / "source-map.json", members)
    feature_names = {}
    for package in kernel_metadata["packages"]:
        key = package["name"] + "@" + package["version"]
        value = sorted(package["features"])
        if key in feature_names and feature_names[key] != value:
            raise ValueError("Ambiguous package feature identity: " + key)
        feature_names[key] = value
    json_output(generated / "feature-names.json", feature_names)
    json_output(generated / "build-script-packages.json", sorted(
        package["name"] + "@" + package["version"]
        for package in kernel_metadata["packages"]
        if any("custom-build" in target["kind"] for target in package["targets"])
    ))
    components = [
        {"name": package["name"], "relative_path": members[package["name"]],
         "dependencies": [{"name": dependency["name"]} for dependency in package["dependencies"]]}
        for package in metadata["packages"]
        if any(dependency["name"] == "component" for dependency in package["dependencies"])
    ]
    json_output(generated / "components.json", components)

    hashes = temporary / "crate-hashes.json"
    hashes.write_text((generated / "crate-hashes.json").read_text())
    for name, manifest in [("Cargo.nix", "Cargo.toml"),
                           ("kernel-Cargo.nix", "tools/experiments/crate2nix/generated/base/Cargo.toml")]:
        relative_output = str((generated / name).relative_to(root))
        candidate = generated / (name + ".candidate")
        try:
            subprocess.run(
                [generator + "/bin/crate2nix", "generate", "-f", manifest,
                 "-o", str(candidate.relative_to(root)), "-h", str(hashes)], env=env, check=True,
            )
            if name == "kernel-Cargo.nix":
                subprocess.run(
                    ["patch", "--fuzz=0", str(candidate), str(generated.parent / "resolver2.patch")],
                    check=True,
                )
            lines = candidate.read_text().splitlines()
            lines[2] = f'#   "generate" "-f" "{manifest}" "-o" "{relative_output}"'
            content = "\n".join(lines).rstrip() + "\n"
            output(generated / name, content)
        finally:
            candidate.unlink(missing_ok=True)
    json_output(generated / "crate-hashes.json", json.loads(hashes.read_text()))

if stale:
    print("Stale generated inputs:" if args.check else "Updated generated inputs:")
    print("\n".join(stale))
raise SystemExit(bool(stale) if args.check else 0)
