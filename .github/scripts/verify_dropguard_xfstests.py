#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Verify fixed full-list xfstests evidence without rerunning any test."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("qemu_log", type=Path)
parser.add_argument("full_list", type=Path)
parser.add_argument("block_list", type=Path)
parser.add_argument("--expected-count", type=int, default=86)
args = parser.parse_args()
case_pattern = r"[A-Za-z0-9_-]+/[0-9]{3}"


def names(path):
    entries = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        entry = line.split("#", 1)[0].strip()
        if entry:
            assert re.fullmatch(case_pattern, entry), (str(path), number, entry)
            entries.append(entry)
    assert len(entries) == len(set(entries)), (str(path), "duplicate input IDs")
    return set(entries)


requested = names(args.full_list)
blocked = names(args.block_list)
expected = requested - blocked
assert len(expected) == args.expected_count, len(expected)
text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", args.qemu_log.read_text()).replace("\r", "")
lines = text.splitlines()
passed, expunged, skipped, unknown = [], [], [], []
for number, line in enumerate(lines, 1):
    if not re.match(rf"^({case_pattern})\b", line):
        continue
    match = re.fullmatch(rf"({case_pattern})\s+(.+?)\s*", line)
    assert match, (number, line)
    case, status = match.groups()
    row = {"case": case, "line": number, "status": status}
    if re.fullmatch(r"[0-9]+s", status):
        passed.append(row)
    elif status == "[expunged]":
        expunged.append(row)
    elif status.startswith("[not run]"):
        skipped.append(row)
    else:
        unknown.append(row)

all_rows = passed + expunged + skipped + unknown
duplicates = {case: count for case, count in Counter(row["case"] for row in all_rows).items() if count != 1}
assert not duplicates, duplicates
assert not unknown, ("failed or unrecognized case rows", unknown)
assert not skipped, ("expected every intended case to pass", skipped)
passed_ids = {row["case"] for row in passed}
expunged_ids = {row["case"] for row in expunged}
assert passed_ids == expected, (sorted(expected - passed_ids), sorted(passed_ids - expected))
assert not passed_ids & blocked, sorted(passed_ids & blocked)
assert expunged_ids == requested & blocked, sorted(expunged_ids)
ran_rows = [(number, line[5:].split()) for number, line in enumerate(lines, 1) if line.startswith("Ran: ")]
assert len(ran_rows) == 1, ran_rows
ran_line, ran_ids = ran_rows[0]
assert len(ran_ids) == len(set(ran_ids)) == len(expected), ran_ids
assert set(ran_ids) == expected, (sorted(expected - set(ran_ids)), sorted(set(ran_ids) - expected))
summaries = [(number, line) for number, line in enumerate(lines, 1) if re.fullmatch(r"Passed all [0-9]+ tests", line)]
assert len(summaries) == 1 and summaries[0][1] == f"Passed all {len(expected)} tests", summaries
assert not any(re.match(r"^(?:Failures:|Failed [0-9]+|Not run:)", line) for line in lines)
markers = [number for number, line in enumerate(lines, 1) if line == "All conformance tests passed."]
assert len(markers) == 1, markers
assert markers[0] > summaries[0][0] > max(row["line"] for row in all_rows)

result = {
    "verification_status": "PASS_EXACT_INTENDED_FULL_LIST",
    "inputs": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (args.qemu_log, args.full_list, args.block_list)},
    "requested_count": len(requested), "expected_count": len(expected),
    "pass_count": len(passed), "skip_count": len(skipped), "fail_count": len(unknown),
    "expunged_count": len(expunged), "passed_ids": sorted(passed_ids),
    "expunged_ids": sorted(expunged_ids), "blocked_ids": sorted(blocked),
    "missing": [], "unexpected": [], "duplicate_case_rows": duplicates,
    "ran_summary_line": ran_line, "passed_summary": summaries[0],
    "conformance_success_marker_line": markers[0],
    "passed_rows": passed, "expunged_rows": expunged,
    "skipped_rows": skipped, "failed_or_unrecognized_rows": unknown,
}
print(json.dumps(result, indent=2))
