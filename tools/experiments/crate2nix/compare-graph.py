#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Compare realized Nix compilation instances with Cargo's frozen unit graph."""

import argparse
import json
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("cargo_units")
parser.add_argument("cargo_metadata")
parser.add_argument("nix_graph")
args = parser.parse_args()
with open(args.cargo_units) as source:
    units = json.load(source)["units"]
with open(args.cargo_metadata) as source:
    packages = {package["id"]: package for package in json.load(source)["packages"]}
with open(args.nix_graph) as source:
    nix_graph = json.load(source)

stdlib = {"core", "alloc", "compiler_builtins", "rustc-std-workspace-core"}


def cargo_key(unit):
    if "custom-build" in unit["target"]["kind"]:
        return None
    package = packages.get(unit["pkg_id"])
    if package:
        name, version = package["name"], package["version"]
    else:
        fragment = unit["pkg_id"].rsplit("#", 1)[1]
        if "@" in fragment:
            name, version = fragment.rsplit("@", 1)
        else:
            name, version = unit["target"]["name"], fragment
    if name in stdlib:
        return None
    return (name, version, "target" if unit["platform"] else "host")


def cargo_build_dependencies(unit):
    pending = [dep["index"] for dep in unit["dependencies"]
               if "custom-build" in units[dep["index"]]["target"]["kind"]
               and units[dep["index"]]["pkg_id"] == unit["pkg_id"]]
    seen, result = set(), set()
    while pending:
        index = pending.pop()
        if index in seen:
            continue
        seen.add(index)
        script = units[index]
        for dependency in script["dependencies"]:
            child = units[dependency["index"]]
            if "custom-build" in child["target"]["kind"]:
                if child["pkg_id"] == unit["pkg_id"]:
                    pending.append(dependency["index"])
            elif script["mode"] == "build" and (identity := cargo_key(child)) is not None:
                result.add(identity)
    return sorted(result)


expected = {}
errors = []
for unit in units:
    key = cargo_key(unit)
    if key is None:
        continue
    value = {
        "features": sorted(unit["features"]),
        "buildDependencies": cargo_build_dependencies(unit),
        "dependencies": sorted(
            dependency_key
            for dep in unit["dependencies"]
            if (dependency_key := cargo_key(units[dep["index"]])) is not None
        ),
    }
    if key in expected and expected[key] != value:
        errors.append({"instance": key, "error": "multiple Cargo instances require a finer identity"})
    expected[key] = value

by_drv = {row["drvPath"]: row for row in nix_graph}


def nix_key(row):
    return (row["name"], row["version"], row["role"])


actual = {
    nix_key(row): {
        "features": sorted(set(row["features"])),
        "dependencies": sorted(nix_key(by_drv[dep]) for dep in row["dependencies"]),
        "buildDependencies": sorted(nix_key(by_drv[dep]) for dep in row["buildDependencies"]),
    }
    for row in nix_graph
}
for key in sorted(expected.keys() | actual.keys()):
    if expected.get(key) != actual.get(key):
        errors.append({"instance": key, "cargo": expected.get(key), "nix": actual.get(key)})
print(json.dumps({"cargo_instances": len(expected), "nix_instances": len(actual), "differences": errors}, indent=2))
sys.exit(bool(errors))
