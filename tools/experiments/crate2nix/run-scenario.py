#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Measure one realization and verify its source invalidation boundary."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("scenario", choices=["same-input", "docs-only", "assembler-change", "core-change", "library-change", "negative"])
parser.add_argument("--seed-graph", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--max-jobs", type=int, default=4)
parser.add_argument("--cores", type=int, default=2)
parser.add_argument("--probe", action="store_true")
parser.add_argument("--require-substitute", action="store_true")
parser.add_argument("--fresh-runner", action="store_true")
parser.add_argument("--sample-name")
args = parser.parse_args()
root = Path(__file__).resolve().parents[3]
args.output = args.output.resolve()
args.output.mkdir(parents=True, exist_ok=False)
metrics_path = args.output / "metrics.json"
metrics_path.write_text(json.dumps({
    "scenario": args.sample_name or args.scenario, "exit_code": None,
    "validation_passed": False, "valid_performance_sample": False,
    "failure_reason": "Sample did not complete validation",
}, indent=2) + "\n")
seed = json.loads(args.seed_graph.read_text())
fixtures = {
    "docs-only": ("book/src/SUMMARY.md", "\n<!-- crate2nix docs-only fixture -->\n", None),
    "assembler-change": ("kernel/src/lib.rs", "\nconst _: () = ();\n", "asterinas"),
    "core-change": ("kernel/core/src/lib.rs", "\nconst _: () = ();\n", "aster-core"),
    "library-change": ("ostd/libs/ostd-pod/src/lib.rs", "\nconst _: () = ();\n", "ostd-pod"),
    "negative": ("kernel/src/lib.rs", '\ncompile_error!("C2N_NEGATIVE_INVALIDATION");\n', "asterinas"),
}
if args.probe:
    fixtures["negative"] = ("ostd/libs/ostd-pod/src/lib.rs", '\ncompile_error!("C2N_NEGATIVE_INVALIDATION");\n', "ostd-pod")
fixture = fixtures.get(args.scenario)
original = None
if fixture:
    filename = root / fixture[0]
    original = filename.read_bytes()
    modified = original + fixture[1].encode()
    filename.write_bytes(modified)


def key(row):
    return (row["name"], row["version"], row["role"])


try:
    started = time.monotonic()
    graph = json.loads(subprocess.check_output(
        ["nix", "eval", "--json", "--option", "allow-import-from-derivation", "false",
         ".#legacyPackages.x86_64-linux.c2n-exp." + ("host-graph" if args.probe else "graph")], cwd=root,
    ))
    evaluation_seconds = time.monotonic() - started
    (args.output / "crate-graph.json").write_text(json.dumps(graph, indent=2) + "\n")
    previous = {key(row): row for row in seed}
    current = {key(row): row for row in graph}
    if previous.keys() != current.keys():
        raise RuntimeError("A source-only fixture changed the compilation-instance set")
    changed = {identity for identity in previous if previous[identity]["drvPath"] != current[identity]["drvPath"]}
    expected_drvs = {
        row["drvPath"] for row in seed if fixture and row["name"] == fixture[2]
    }
    while True:
        expanded = expected_drvs | {
            row["drvPath"] for row in seed
            if expected_drvs.intersection(row["dependencies"] + row["buildDependencies"])
        }
        if expanded == expected_drvs:
            break
        expected_drvs = expanded
    expected = {key(row) for row in seed if row["drvPath"] in expected_drvs}
    if changed != expected:
        raise RuntimeError(f"Invalidation differs from the reverse dependency closure: {changed ^ expected}")

    build_command = [
        "nix", "build", ".#c2n-exp-" + ("host-probe" if args.probe else "kernel-dev"), "--no-link", "--json",
        "--option", "allow-import-from-derivation", "false",
        "--max-jobs", "0" if args.require_substitute else str(args.max_jobs), "--cores", str(args.cores),
        "--log-format", "internal-json", "-v", "-L",
    ]
    if args.require_substitute:
        build_command += ["--builders", ""]
    present_before = sorted(output for row in graph for output in row["outputs"] if Path(output).exists())
    if args.fresh_runner and present_before:
        raise RuntimeError("Project compilation outputs already exist on the purportedly fresh runner")
    started = time.monotonic()
    with (args.output / "build-result.json").open("w") as stdout, (args.output / "build-events.jsonl").open("w") as stderr:
        result = subprocess.run(build_command, cwd=root, stdout=stdout, stderr=stderr, check=False)
    elapsed = time.monotonic() - started
    realized_output = None
    if result.returncode == 0:
        realized = json.loads((args.output / "build-result.json").read_text())
        if len(realized) != 1 or len(realized[0]["outputs"]) != 1:
            raise RuntimeError("Expected one selected package output")
        realized_output = next(iter(realized[0]["outputs"].values()))
        with (args.output / "output-path-info.json").open("w") as info:
            subprocess.run(["nix", "path-info", "--json", "--json-format", "1", realized_output],
                           stdout=info, check=True)
    events = [json.loads(line.removeprefix("@nix "))
              for line in (args.output / "build-events.jsonl").read_text().splitlines()
              if line.startswith("@nix ")]
    built = {event["fields"][0] for event in events
             if event.get("action") == "start" and event.get("type") == 105 and event.get("fields")}
    project_built = sorted(built.intersection(row["drvPath"] for row in graph))
    expected_builds = None
    if args.fresh_runner and args.scenario != "negative":
        expected_builds = ({row["drvPath"] for row in graph} if args.sample_name == "nix-cold"
                           else {current[identity]["drvPath"] for identity in changed})
    metrics = {
        "scenario": args.sample_name or args.scenario,
        "environment": "github-runner" if os.environ.get("GITHUB_ACTIONS") else "local-populated-store",
        "final_output_remote_substitution_proven": bool(
            args.require_substitute and result.returncode == 0 and not present_before and not project_built
        ),
        "compilation_instances": len(graph),
        "realized_output": realized_output,
        "cpu_model": next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                           if line.startswith("model name")), "unknown"),
        "logical_cpus": os.cpu_count(),
        "nix_version": subprocess.check_output(["nix", "--version"], text=True).strip(),
        "project_builds_started": project_built,
        "expected_project_builds": sorted(expected_builds) if expected_builds is not None else None,
        "project_build_count": len(project_built), "present_outputs_before": present_before,
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "runner": os.environ.get("RUNNER_NAME"), "run_id": os.environ.get("GITHUB_RUN_ID"),
        "changed_instances": sorted(changed), "unchanged_instances": len(graph) - len(changed),
        "evaluation_seconds": evaluation_seconds, "realization_seconds": elapsed,
        "exit_code": result.returncode, "command": build_command,
    }
    if args.scenario == "negative":
        validated = bool(result.returncode != 0 and project_built
                         and "C2N_NEGATIVE_INVALIDATION" in (args.output / "build-events.jsonl").read_text())
    else:
        validated = result.returncode == 0
        if expected_builds is not None and set(project_built) != expected_builds:
            validated = False
            metrics["failure_reason"] = "Fresh sample did not build exactly the expected project derivations"
    metrics["validation_passed"] = validated
    metrics["valid_performance_sample"] = validated and args.scenario != "negative"
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n")
    if not validated:
        raise RuntimeError(metrics.get("failure_reason", "Sample failed realization or scenario validation; retain its metrics and logs"))
    print(json.dumps(metrics, indent=2))
except Exception as error:
    metrics = json.loads(metrics_path.read_text())
    metrics.update(validation_passed=False, valid_performance_sample=False, failure_reason=str(error))
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n")
    raise
finally:
    if original is not None:
        if not filename.exists() or filename.read_bytes() != modified:
            metrics = json.loads(metrics_path.read_text())
            metrics.update(validation_passed=False, valid_performance_sample=False,
                           failure_reason="Concurrent edit prevented fixture restoration")
            metrics_path.write_text(json.dumps(metrics, indent=2) + "\n")
            raise RuntimeError("Concurrent edit detected; leave the fixture file intact for inspection")
        filename.write_bytes(original)
