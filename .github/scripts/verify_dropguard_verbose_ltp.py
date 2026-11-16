#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Preserve and verify the per-case output of Kirk v4.1.0's verbose UI."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("qemu_log", type=Path)
parser.add_argument("runtest", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--expected-count", type=int, default=71)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
rows = [row.split() for row in args.runtest.read_text().splitlines()]
expected = {row[0]: " ".join(row[1:]) for row in rows}
assert len(rows) == len(expected) == args.expected_count
text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", args.qemu_log.read_text()).replace("\r", "")
header = r"^─+\n {6}([A-Za-z0-9_]+)\n─+\nExecuting: ([^\n]+)\n\n"
headers = list(re.finditer(header, text, re.MULTILINE))
results = []
for index, match in enumerate(headers):
    name, command = match.groups()
    end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
    section = text[match.start():end]
    duration = re.search(r"^Duration: (.+)$", section, re.MULTILINE)
    body = section[:duration.start()] if duration else section
    counters = re.findall(r"^(passed|failed|broken|skipped|warnings)\s+([0-9]+)\s*$", body, re.MULTILINE)
    keys = [key for key, value in counters[-5:]]
    complete = bool(duration) and keys == ["passed", "failed", "broken", "skipped", "warnings"]
    stats = {key: int(value) for key, value in counters[-5:]} if complete else {}
    timed_out = "Test timed out" in body
    status = "incomplete"
    if complete:
        if stats["failed"]:
            status = "fail"
        elif timed_out or stats["broken"]:
            status = "broken"
        elif stats["skipped"]:
            status = "skip"
        else:
            status = "pass"
    case_path = args.output / (name + ".log")
    case_path.write_text(section)
    results.append({"name": name, "command": command, "status": status,
                    "stats": stats, "timed_out": timed_out,
                    "duration": duration.group(1) if duration else None,
                    "command_matches_original": " ".join(command.split()) == expected.get(name),
                    "line": text.count("\n", 0, match.start()) + 1,
                    "output_file": str(case_path)})
    if name == "pipe13":
        (args.output / "pipe13-output.log").write_text(section)

names = [item["name"] for item in results]
coverage = {"expected": list(expected), "results": results,
            "counts": dict(Counter(item["status"] for item in results)),
            "missing": sorted(expected.keys() - set(names)),
            "extra": sorted(set(names) - expected.keys()),
            "duplicates": {name: count for name, count in Counter(names).items() if count != 1},
            "command_mismatches": [item["name"] for item in results if not item["command_matches_original"]],
            "failed_or_broken": [item["name"] for item in results if item["status"] in {"fail", "broken"}],
            "incomplete": [item["name"] for item in results if item["status"] == "incomplete"],
            "warnings": [item["name"] for item in results if item["stats"].get("warnings", 0)],
            "final_success_marker": "All conformance tests passed." in text}
(args.output / "coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
print(json.dumps(coverage, indent=2))
assert len(names) == len(set(names)) == args.expected_count
assert set(names) == expected.keys()
assert not coverage["command_mismatches"] and not coverage["incomplete"]
assert not coverage["failed_or_broken"] and not coverage["warnings"]
assert coverage["final_success_marker"]
