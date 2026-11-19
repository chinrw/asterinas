#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Print all shard logs and reconcile their results with the build's manifest."""

import argparse
from collections import Counter
import csv
import os
from pathlib import Path
import re
import sys
import uuid

from linux_regression_groups import shard_owners


def read_rows(path):
    with path.open(newline="") as source:
        return list(csv.DictReader(source, delimiter="\t", quoting=csv.QUOTE_NONE))


def workload(row):
    return f"{row['group']}/{row['case']}"


def collect_results(directory, shards, expected):
    results = {}
    errors = []
    for index in range(shards):
        path = directory / f"shard-{index}" / "results.tsv"
        if not path.is_file():
            errors.append(f"Missing results from shard {index}")
            continue
        required = {"group", "case", "result", "duration_seconds", "command", "reason"}
        for line, row in enumerate(read_rows(path), start=2):
            if not required.issubset(row) or None in row or None in row.values():
                errors.append(f"Incomplete result in shard {index} at line {line}")
                continue
            key = workload(row)
            if key in results or key not in expected:
                errors.append(
                    f"Duplicate or unexpected result for {key} in shard {index}"
                )
                continue
            results[key] = (index, row)
    return results, errors


def print_log(log, result, label, output):
    print(f"::group::{result} {label}", file=output)
    # Guest output is arbitrary text and must not issue Actions commands.
    token = uuid.uuid4().hex
    print(f"::stop-commands::{token}", file=output)
    with log.open(errors="replace", newline="\n") as source:
        for line in source:
            print(line.rstrip("\r\n"), file=output)
    print(f"::{token}::", file=output)
    print("::endgroup::", file=output)


def log_details(log):
    first_error = None
    skipped_subtests = []
    with log.open(errors="replace", newline="\n") as source:
        for line in source:
            line = line.rstrip("\r\n")
            if skipped := re.fullmatch(r"(test_\w+) skipped: (.+)", line):
                skipped_subtests.append(f"{skipped[1]} ({skipped[2]})")
            if first_error is None and re.search(
                r"failed \[|fatal error|^Error:", line, re.I
            ):
                first_error = line
    return first_error, skipped_subtests


def report(
    manifest,
    directory,
    shards,
    allow_all_skipped=False,
    summary_path=None,
    console_output=None,
    mode=None,
):
    cases = read_rows(manifest)
    expected = {workload(case) for case in cases}
    if not expected or len(expected) != len(cases):
        raise ValueError("Empty manifest or duplicate workload IDs")

    results, errors = collect_results(directory, shards, expected)
    recorded_modes = {
        line.removeprefix("guest_mode=")
        for metadata in directory.glob("shard-*/environment.txt")
        for line in metadata.read_text().splitlines()
        if line.startswith("guest_mode=")
    }
    if mode is None:
        mode = next(iter(sorted(recorded_modes)), "case")
    if recorded_modes - {mode}:
        errors.append("Shard guest modes disagree with the report")
    totals = Counter()
    printed_groups = set()
    table = []
    for case, owner in zip(cases, shard_owners(cases, shards, mode)):
        key = workload(case)
        action = case["action"]
        index, row = results.get(key, (owner, None))
        result, duration, detail = "MISSING", "-", "No workload result was collected"
        if row is not None:
            result, duration, detail = (
                row["result"],
                row["duration_seconds"] + "s",
                row["reason"],
            )
            if (
                index != owner
                or row["command"].strip() != case["command"].strip()
                or result not in {"PASS", "FAIL", "SKIP", "NOT_RUN"}
                or (action == "SKIP") != (result == "SKIP")
            ):
                errors.append(f"Result for {key} does not match the manifest")

        folder = directory / f"shard-{index}" / case["group"]
        log = folder / (case["case"] + ".log")
        label = f"{key} ({case['command'].strip().removeprefix('./')})"
        if mode == "subsystem" and action == "RUN" and folder not in printed_groups:
            printed_groups.add(folder)
            if (folder / "kernel.log").is_file():
                print_log(folder / "kernel.log", "Kernel", case["group"], console_output)
            if (folder / "guest.log").is_file():
                print_log(
                    folder / "guest.log", "Subsystem", case["group"], console_output
                )
            else:
                errors.append(f"Missing subsystem console for {case['group']}")
            if (folder / "guest.error").is_file():
                errors.append((folder / "guest.error").read_text().strip())
        if mode == "case" and log.with_suffix(".kernel.log").is_file():
            print_log(log.with_suffix(".kernel.log"), "Kernel", label, console_output)
        if log.is_file():
            if mode == "case":
                print_log(log, result, label, console_output)
            first_error, skipped_subtests = log_details(log)
            if result == "FAIL" and detail == "workload failed" and first_error:
                detail = first_error
            if skipped_subtests:
                detail = " ".join(
                    filter(
                        None,
                        [detail, "Skipped subtests: " + ", ".join(skipped_subtests)],
                    )
                )
        elif result in {"PASS", "FAIL"}:
            errors.append(f"Missing console log for {key}")
        totals[result] += 1
        table.append((label, result, duration, detail))

    for message in errors:
        print(f"Report error: {message}")
    # Details can also contain assertion text supplied by a guest.
    token = uuid.uuid4().hex
    print(f"::stop-commands::{token}")
    if mode == "subsystem":
        print("\nSUBSYSTEM TIMINGS")
        print("SUBSYSTEM       WORKLOADS  GUEST WALL TIME")
        for path in sorted(directory.glob("shard-*/guest-times.tsv")):
            for item in read_rows(path):
                print(
                    f"{item['group']:<15} {item['workloads']:>9}  {item['wall_seconds']}s"
                )
    width = max(len(row[0]) for row in table)
    print("\nTEST RESULTS")
    print(f"{'TEST':<{width}}  RESULT   TIME  DETAILS")
    for name, result, duration, detail in table:
        print(f"{name:<{width}}  {result:<7}  {duration:>4}  {detail}".rstrip())
    print("\nTEST SUMMARY")
    print(f"Registered: {len(cases)}")
    print(f"Executed: {totals['PASS'] + totals['FAIL']}")
    for result in ("PASS", "FAIL", "SKIP", "NOT_RUN", "MISSING"):
        print(f"{result}: {totals[result]}")
    print(f"::{token}::")
    if summary_path:
        with open(summary_path, "a") as output:
            print("### Linux regression", file=output)
            print(
                f"\n{len(cases)} workloads registered. Open **Test summary** for individual results and **Full workload logs** for their console output.",
                file=output,
            )
            print("\n| Result | Workloads |\n| --- | ---: |", file=output)
            for result in ("PASS", "FAIL", "SKIP", "NOT_RUN", "MISSING"):
                print(f"| {result} | {totals[result]} |", file=output)
    return bool(
        errors
        or totals["FAIL"]
        or totals["NOT_RUN"]
        or totals["MISSING"]
        or (not allow_all_skipped and totals["SKIP"] == len(cases))
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--shards", required=True, type=int, choices=range(1, 257), metavar="N"
    )
    parser.add_argument("--allow-all-skipped", action="store_true")
    parser.add_argument(
        "--console-output", type=argparse.FileType("w", encoding="utf-8")
    )
    parser.add_argument(
        "--mode",
        choices=("subsystem", "case"),
        default=os.environ.get("REGRESSION_GUEST_MODE"),
    )
    args = parser.parse_args()
    sys.exit(
        report(
            args.manifest,
            args.directory,
            args.shards,
            args.allow_all_skipped,
            os.environ.get("GITHUB_STEP_SUMMARY"),
            args.console_output,
            args.mode,
        )
    )
