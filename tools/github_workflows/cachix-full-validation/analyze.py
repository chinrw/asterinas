# SPDX-License-Identifier: MPL-2.0
"""Summarize the immutable artifacts from the populated-cache experiment."""

import argparse
import json
from pathlib import Path
import re
import statistics

from common import PRE_GC_SNAPSHOT_COUNT, compare_closures
from measure import summarize_scans


def read_json(path):
    return json.loads(path.read_text())


def parse_log(path):
    vertices = {}
    events = []
    for line_no, line in enumerate(path.read_text().splitlines(), 1):
        match = re.match(r"#(\d+) \[measured \d+/\d+\] RUN (.*)", line)
        if match:
            command = match[2]
            vertices[match[1]] = command
        match = re.match(r"#(\d+)\s+([\d.]+)\s+(.*)", line)
        if match and match[1] in vertices:
            events.append((vertices[match[1]], float(match[2]), match[3], line_no))
    stages = {}
    latest_deletion = {}
    redownloads = []
    for command, at, text, line_no in events:
        row = stages.setdefault(command, {"downloads": [], "builds": [], "deletions": [], "network_warnings": []})
        copied = re.search(r"copying path '([^']+)' from '([^']+)'", text)
        if copied:
            path, cache = copied.groups()
            row["downloads"].append({"path": path, "cache": cache, "at_s": at, "line": line_no})
            if path in latest_deletion and latest_deletion[path]["stage"] != command:
                redownloads.append({"path": path, "cache": cache, "stage": command, "line": line_no, "last_deletion": latest_deletion[path]})
        built = re.search(r"building '([^']+\.drv)'", text)
        if built:
            row["builds"].append({"drv": built[1], "at_s": at, "line": line_no})
        deleted = re.fullmatch(r"deleting '(/nix/store/[^']+)'", text)
        if deleted:
            path = deleted[1]
            row["deletions"].append(path)
            latest_deletion[path] = {"stage": command, "at_s": at, "line": line_no}
        if text.startswith(("warning:", "error:")) and re.search(r"retry|HTTP error|timed out|connection|download", text, re.I):
            row["network_warnings"].append({"line": line_no, "message": text})
    return {"stages": stages, "gc_redownloads": redownloads}


def cpu_seconds(stage):
    before = stage.get("cgroup_before", {}).get("cpu.stat", "")
    after = stage.get("cgroup_after", {}).get("cpu.stat", "")
    def values(text):
        return {key: int(value) for key, value in (line.split() for line in text.splitlines())}
    before_values, after_values = values(before), values(after)
    if "usage_usec" in before_values and "usage_usec" in after_values:
        return (after_values["usage_usec"] - before_values["usage_usec"]) / 1e6
    return None


def analyze_case(folder, kind, run, producer_closure, expected_roots):
    case_folder = folder / f"{run['index']}-{run['mode']}"
    verified = (case_folder / "verify/result.json").exists() and read_json(case_folder / "verify/result.json")["result"] == "passed"
    parsed = parse_log(case_folder / "build.log")
    downloads = [event for stage in parsed["stages"].values() for event in stage["downloads"]]
    test_downloads = [event for event in downloads if event["cache"] == "https://asterina-test.cachix.org"]
    hash_comparisons = run.get("root_hash_comparison", {})
    stages = [{"command": " ".join(stage["command"]), "elapsed_s": stage["elapsed_s"], "cpu_seconds": cpu_seconds(stage), "profile_store": stage["profile_store"], "disk_sampling": summarize_scans(stage.get("samples", [])), "scan_seconds": stage.get("scan_seconds", 0)} for stage in run["stages"]]
    cpu_measurements = [stage["cpu_seconds"] for stage in stages]
    cpu_total = sum(cpu_measurements) if cpu_measurements and all(value is not None for value in cpu_measurements) else None
    case = {
        "index": run["index"], "mode": run["mode"], "build_export_s": run["elapsed_s"],
        "command_sum_s": sum(stage["elapsed_s"] for stage in stages),
        "image_size_bytes": run["image_size_bytes"], "verified": verified,
        "source_builder_events": sum(len(stage["builds"]) for stage in parsed["stages"].values()),
        "test_cache_download_events": len(test_downloads),
        "test_cache_unique_downloads": len({event["path"] for event in test_downloads}),
        "gc_redownload_events": len(parsed["gc_redownloads"]),
        "gc_redownload_paths": len({event["path"] for event in parsed["gc_redownloads"]}),
        "root_hashes_equal": sum(row["equal"] for row in hash_comparisons.values()),
        "root_hashes_compared": len(hash_comparisons),
        "hash_differences": {path: row for path, row in hash_comparisons.items() if not row["equal"]},
        "initramfs_cache_http": run.get("initramfs_cache_http"), "stages": stages,
        "command_cpu_s": cpu_total,
        "log_detail": "verbose" if kind == "profile" else "production quiet",
        "root_signers": run.get("root_signers", {}),
        "test_cache_signed_roots": sum("asterina-test.cachix.org-1" in signers for signers in run.get("root_signers", {}).values()),
        "network_warnings": [event for stage in parsed["stages"].values() for event in stage["network_warnings"]],
    }
    host_samples = [json.loads(line) for line in (case_folder / "host.jsonl").read_text().splitlines()]
    case["host_filesystem"] = {
        "sample_path": host_samples[0]["filesystem_sample_path"],
        "initial_used_bytes": host_samples[0]["filesystem_used_bytes"],
        "max_observed_used_bytes": max(row["filesystem_used_bytes"] for row in host_samples),
        "minimum_available_bytes": min(row["filesystem_available_bytes"] for row in host_samples),
        "scope": "filesystem containing artifacts; includes retained prior build images and other host data",
    }
    closure_path = case_folder / "verify/runtime-closure.json"
    closure = read_json(closure_path) if closure_path.exists() else None
    case["closure_comparison"] = compare_closures(producer_closure, closure, expected_roots)
    if kind == "profile":
        case["pre_gc_snapshots"] = run.get("pre_gc_snapshots", [])
        scans = [row for stage in run["stages"] for row in stage.get("samples", [])]
        case["disk_sampling"] = summarize_scans(scans + case["pre_gc_snapshots"])
        case["sampler_errors"] = [error for stage in run["stages"] for error in stage.get("sampler_errors", [])]
        case["profile_valid"] = (
            case["disk_sampling"]["status"] == "valid"
            and not case["sampler_errors"]
            and bool(stages)
            and all(stage["profile_store"] and stage["disk_sampling"]["status"] == "valid" for stage in stages)
            and len(case["pre_gc_snapshots"]) == PRE_GC_SNAPSHOT_COUNT
        )
    else:
        assert not any(stage["profile_store"] for stage in stages), "Timing sample performed store scans"
    return case


def compare_timings(folder, cases, expected_roots, bootstrap_s, byte_equal):
    baseline = [case["build_export_s"] for case in cases if case["mode"] == "baseline"]
    candidate = [case["build_export_s"] for case in cases if case["mode"] == "candidate"]
    comparison = {
        "baseline_mean_s": statistics.mean(baseline), "baseline_range_s": [min(baseline), max(baseline)],
        "candidate_mean_s": statistics.mean(candidate), "candidate_range_s": [min(candidate), max(candidate)],
        "time_reduction_percent": 100 * (1 - statistics.mean(candidate) / statistics.mean(baseline)),
        "speedup_factor": statistics.mean(baseline) / statistics.mean(candidate),
        "paired_time_reduction_percent": [100 * (1 - cases[1]["build_export_s"] / cases[0]["build_export_s"]), 100 * (1 - cases[2]["build_export_s"] / cases[3]["build_export_s"])],
        "byte_equivalence_passed": byte_equal,
        "interpretation": "measured workload timing; full closure equivalence failed or is incomplete" if not byte_equal else "all runtime closure paths, hashes, and reference sets match",
        "stages": [],
    }
    comparison["with_shared_bootstrap"] = None
    if bootstrap_s is not None:
        comparison["with_shared_bootstrap"] = {
            "baseline_composite_s": bootstrap_s + statistics.mean(baseline),
            "candidate_composite_s": bootstrap_s + statistics.mean(candidate),
            "time_reduction_percent": 100 * (1 - (bootstrap_s + statistics.mean(candidate)) / (bootstrap_s + statistics.mean(baseline))),
            "scope": "sum of separately measured shared bootstrap and consumer means",
        }
    comparison["repeat_byte_comparison"] = {}
    for mode in ("baseline", "candidate"):
        selected = [case for case in cases if case["mode"] == mode]
        paths = [folder / f"{case['index']}-{case['mode']}" / "verify/runtime-closure.json" for case in selected]
        first, second = [read_json(path) if path.exists() else None for path in paths]
        comparison["repeat_byte_comparison"][mode] = compare_closures(first, second, expected_roots)
        comparison[f"{mode}_image_size_mean_bytes"] = statistics.mean(case["image_size_bytes"] for case in selected)
        comparison[f"{mode}_image_size_range_bytes"] = [min(case["image_size_bytes"] for case in selected), max(case["image_size_bytes"] for case in selected)]
        cpu_totals = [case["command_cpu_s"] for case in selected]
        comparison[f"{mode}_command_cpu_mean_s"] = statistics.mean(cpu_totals) if all(value is not None for value in cpu_totals) else None
        comparison[f"{mode}_command_mean_s"] = statistics.mean(case["command_sum_s"] for case in selected)
    for index in range(len(cases[0]["stages"])):
        baseline_stages = [case["stages"][index]["elapsed_s"] for case in cases if case["mode"] == "baseline"]
        candidate_stages = [case["stages"][index]["elapsed_s"] for case in cases if case["mode"] == "candidate"]
        comparison["stages"].append({"command": cases[0]["stages"][index]["command"], "baseline_mean_s": statistics.mean(baseline_stages), "baseline_range_s": [min(baseline_stages), max(baseline_stages)], "candidate_mean_s": statistics.mean(candidate_stages), "candidate_range_s": [min(candidate_stages), max(candidate_stages)], "time_reduction_percent": 100 * (1 - statistics.mean(candidate_stages) / statistics.mean(baseline_stages))})
    return comparison


def analyze(base):
    output = {"hosts": {}, "incomplete": [], "validation_failures": []}
    for host in ("x86_64", "aarch64"):
        combined = {"producer": None, "timing": None, "profile": None}
        publisher = base / f"published-{host}"
        if (publisher / "summary.json").exists():
            combined["producer"] = read_json(publisher / "summary.json")
        producer_closure = read_json(publisher / "published-closure.json") if (publisher / "published-closure.json").exists() else None
        expected_roots = {path for row in read_json(publisher / "expected-roots.json") for path in row["paths"]} if (publisher / "expected-roots.json").exists() else set()
        for kind in ("timing", "profile"):
            folder = base / f"consumers-{host}-{kind}"
            if not (folder / "summary.json").exists():
                output["incomplete"].append(f"{host}-{kind}: no artifact")
                continue
            summary = read_json(folder / "summary.json")
            cases = [analyze_case(folder, kind, run, producer_closure, expected_roots) for run in summary["runs"]]
            bootstrap = summary.get("bootstrap")
            bootstrap_s = bootstrap.get("elapsed_s") if bootstrap and bootstrap.get("exit_code") == 0 else None
            incomplete_reasons = []
            if bootstrap_s is None:
                incomplete_reasons.append("bootstrap timing unavailable or bootstrap failed")
            combined[kind] = {
                "complete": summary["complete"], "kernel_smoke": summary.get("kernel_smoke"),
                "bootstrap_s": bootstrap_s, "cases": cases, "incomplete_reasons": incomplete_reasons,
            }
            expected_order = ["baseline", "candidate", "candidate", "baseline"] if kind == "timing" else ["baseline", "candidate"]
            has_expected_cases = [case["mode"] for case in cases] == expected_order
            measured = bool(expected_roots) and has_expected_cases and all(
                case["verified"] and case["root_hashes_compared"] == len(expected_roots) for case in cases
            )
            byte_equal = measured and all(case["closure_comparison"]["equal"] for case in cases)
            disk_valid = kind != "profile" or (has_expected_cases and all(case["profile_valid"] for case in cases))
            passed = summary["complete"] and measured and byte_equal and disk_valid and bootstrap_s is not None
            combined[kind].update(measurements_complete=measured, byte_equivalence_passed=byte_equal, validation_passed=passed)
            if kind == "timing" and measured:
                combined[kind]["comparison"] = compare_timings(folder, cases, expected_roots, bootstrap_s, byte_equal)
            if not measured:
                incomplete_reasons.append("required measurements missing")
            output["incomplete"].extend(f"{host}-{kind}: {reason}" for reason in incomplete_reasons)
            if not passed:
                output["validation_failures"].append(f"{host}-{kind}: workflow-complete={summary['complete']}; byte-equivalence={byte_equal}; disk-valid={disk_valid}")
        output["hosts"][host] = combined
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = analyze(args.artifacts)
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    for host, data in value["hosts"].items():
        if data["timing"] and "comparison" in data["timing"]:
            print(host, json.dumps(data["timing"]["comparison"]))
    print("Incomplete:", value["incomplete"])
