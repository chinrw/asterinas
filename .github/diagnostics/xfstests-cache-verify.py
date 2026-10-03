#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

import json
from pathlib import Path
import re
import sys


def main():
    log_path = Path(sys.argv[1])
    runlist_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])
    expected = [line.split("#", 1)[0].strip() for line in runlist_path.read_text().splitlines()]
    expected = [line for line in expected if line]
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", log_path.read_text(errors="replace"))
    lines = [line.strip() for line in text.splitlines()]
    ran = [line.removeprefix("Ran: ").split() for line in lines if line.startswith("Ran: ")]
    result_pattern = re.compile(r"^((?:generic|ext4)/\d{3})\s+(\d+)s\s*$")
    results = [match.groups() for line in lines if (match := result_pattern.fullmatch(line))]
    skips = [line for line in lines if re.search(r"\[not run\]|^Not run:", line, re.IGNORECASE)]
    failures = [line for line in lines if line.startswith("Failures:")]
    errors = []
    if len(expected) != 78 or len(set(expected)) != 78:
        errors.append("The checked-out runlist must contain exactly 78 unique tests.")
    if len(ran) != 1 or len(ran[0]) != 78 or set(ran[0]) != set(expected):
        errors.append("The Ran summary does not match all 78 requested tests.")
    if len(results) != 78 or {test for test, _ in results} != set(expected):
        errors.append("Expected one successful timing result for each of the 78 tests.")
    if lines.count("Passed all 78 tests") != 1:
        errors.append("Missing the exact xfstests success summary.")
    if lines.count("All conformance tests passed.") != 1:
        errors.append("Missing the guest conformance success summary.")
    if skips or failures:
        errors.append("The guest reported skipped or failed tests.")
    summary = {
        "expected": len(expected),
        "results": len(results),
        "unique_results": len({test for test, _ in results}),
        "skips": skips,
        "failures": failures,
        "seconds_by_test": {test: int(seconds) for test, seconds in results},
        "errors": errors,
    }
    output_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return bool(errors)


if __name__ == "__main__":
    sys.exit(main())
