#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Summarize retained samples without treating negative tests as timings."""

from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys

source = Path(sys.argv[1])
groups = defaultdict(list)
configurations = list(source.rglob("experiment-config.json"))
expected = []
if configurations:
    if len(configurations) != 1:
        raise SystemExit("Expected one experiment configuration")
    expected = json.loads(configurations[0].read_text()).get("expected_samples", [])
if expected:
    for sample in expected:
        paths = list((source / sample["artifact"]).rglob("metrics.json"))
        if len(paths) == 1:
            value = json.loads(paths[0].read_text())
            if value["scenario"] != sample["scenario"]:
                value["validation_passed"] = False
        else:
            value = {"exit_code": None, "validation_passed": False,
                     "failure_reason": "Missing or ambiguous metrics artifact"}
        groups[sample["scenario"]].append(value)
else:
    for path in source.rglob("metrics.json"):
        value = json.loads(path.read_text())
        groups[value["scenario"]].append(value)
print("# Crate2nix experiment samples\n")
print("These are the retained realization timings. Job setup, seed publication, and complete job durations must be assessed from the accompanying Actions job metadata.\n")
print("| Scenario | Valid samples | Failed or incomplete | Median seconds | Min–max seconds |")
print("| --- | ---: | ---: | ---: | --- |")
for scenario, rows in sorted(groups.items()):
    if scenario == "negative":
        failures = sum(not row.get("validation_passed", False) or row["exit_code"] in [0, None] for row in rows)
        print(f"| {scenario} | {len(rows) - failures} expected compilation failures | {failures} | — | — |")
        continue
    valid = [row["realization_seconds"] for row in rows
             if row["exit_code"] == 0 and row.get("validation_passed", False)
             and row.get("valid_performance_sample", False)]
    failures = len(rows) - len(valid)
    if valid:
        print(f"| {scenario} | {len(valid)} | {failures} | {statistics.median(valid):.2f} | {min(valid):.2f}–{max(valid):.2f} |")
    else:
        print(f"| {scenario} | 0 | {failures} | — | — |")
print("\nA single sample is a correctness trial and does not establish a performance benefit.")
