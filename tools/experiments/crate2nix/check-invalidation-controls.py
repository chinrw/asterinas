#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Check feature and compiler-option isolation without timing modified configurations."""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[3]
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
generated = root / "tools/experiments/crate2nix/generated"
summary = {"validation_passed": False, "realization_performed": False}


def write_json(name, value):
    (output / name).write_text(json.dumps(value, indent=2) + "\n")


def command(argv):
    return subprocess.check_output(argv, cwd=root, text=True)


def graph(name):
    value = json.loads(command(["nix", "eval", "--json", "--option",
                               "allow-import-from-derivation", "false",
                               ".#legacyPackages.x86_64-linux.c2n-exp.graph"]))
    write_json(name, value)
    return value


def identity(row):
    return (row["name"], row["version"], row["role"])


def changes(before, after):
    previous = {identity(row): row for row in before}
    current = {identity(row): row for row in after}
    if previous.keys() != current.keys():
        raise RuntimeError("The control unexpectedly changed the compilation-instance set")
    return {key for key in previous if previous[key]["drvPath"] != current[key]["drvPath"]}


@contextmanager
def edited_inputs(paths):
    before = {path: path.read_bytes() for path in paths}
    expected = dict(before)

    def record_edits():
        expected.update({path: path.read_bytes() if path.exists() else None for path in paths})

    try:
        yield record_edits
    finally:
        for path in paths:
            current = path.read_bytes() if path.exists() else None
            if current != expected[path]:
                raise RuntimeError(f"Concurrent edit detected; preserve inputs for inspection: {path}")
        for path, content in before.items():
            if not path.exists() or path.read_bytes() != content:
                path.write_bytes(content)


write_json("validation.json", summary)
try:
    baseline = graph("baseline-graph.json")
    contract_path = generated / "build-contract.json"
    sysroot_attribute = ".#c2n-exp-sysroot-x86_64.drvPath"
    sysroot_before = command(["nix", "eval", "--raw", sysroot_attribute])
    with edited_inputs([contract_path]) as record:
        contract = json.loads(contract_path.read_text())
        contract["library_rustflags"].append("-C force-frame-pointers=yes")
        contract_path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")
        record()
        modified = graph("flags-graph.json")
        changed = changes(baseline, modified)
        expected = {identity(row) for row in baseline if row["role"] == "target"}
        if changed != expected:
            raise RuntimeError("The compiler-option control did not invalidate exactly the target instances")
        sysroot_after = command(["nix", "eval", "--raw", sysroot_attribute])
        if sysroot_before == sysroot_after:
            raise RuntimeError("The target sysroot incorrectly survived a compiler-option change")
        summary["flags"] = {"changed_instances": sorted(changed),
                            "sysroot_before": sysroot_before, "sysroot_after": sysroot_after}

    manifest = root / "kernel/core/Cargo.toml"
    paths = [manifest] + sorted(path for path in generated.rglob("*") if path.is_file())
    with edited_inputs(paths) as record:
        text = manifest.read_text()
        previous = 'features = ["small_rng", "std_rng"]'
        if text.count(previous) != 1:
            raise RuntimeError("The frozen rand feature fixture no longer matches")
        manifest.write_text(text.replace(previous, 'features = ["small_rng", "std_rng", "alloc"]'))
        record()
        try:
            with (output / "feature-generation.log").open("w") as log:
                subprocess.run(["python3", "tools/experiments/crate2nix/generate.py"],
                               cwd=root, stdout=log, stderr=subprocess.STDOUT, check=True)
        finally:
            record()
        modified = graph("feature-graph.json")
        changed = changes(baseline, modified)
        expected_drvs = {row["drvPath"] for row in baseline
                         if row["role"] == "target" and row["name"] in ["rand", "aster-core"]}
        while True:
            expanded = expected_drvs | {row["drvPath"] for row in baseline
                                       if expected_drvs.intersection(row["dependencies"] + row["buildDependencies"])}
            if expanded == expected_drvs:
                break
            expected_drvs = expanded
        expected = {identity(row) for row in baseline if row["drvPath"] in expected_drvs}
        if changed != expected:
            raise RuntimeError("The dependency-feature control exceeded or missed its dependency closure")
        base_manifest = "tools/experiments/crate2nix/generated/base/Cargo.toml"
        metadata = command(["cargo", "metadata", "--locked", "--format-version=1", "--manifest-path", base_manifest])
        (output / "cargo-metadata.json").write_text(metadata)
        contract = json.loads(contract_path.read_text())
        units = command(["cargo", "build", "--locked", "--manifest-path", base_manifest,
                         "--target", "x86_64-unknown-none", "--unit-graph", "-Zunstable-options"]
                        + contract["build_std_args"])
        (output / "cargo-units.json").write_text(units)
        with (output / "feature-comparison.json").open("w") as comparison:
            subprocess.run(["python3", "tools/experiments/crate2nix/compare-graph.py",
                            str(output / "cargo-units.json"), str(output / "cargo-metadata.json"),
                            str(output / "feature-graph.json")], cwd=root, stdout=comparison, check=True)
        summary["dependency_feature"] = {"changed_instances": sorted(changed), "cargo_graph_matches": True}

    if graph("restored-graph.json") != baseline:
        raise RuntimeError("Control fixtures were not fully restored")
    summary["validation_passed"] = True
except Exception as error:
    summary["failure_reason"] = str(error)
    raise
finally:
    write_json("validation.json", summary)
