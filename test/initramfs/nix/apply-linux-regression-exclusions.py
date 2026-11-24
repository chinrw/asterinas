#!/usr/bin/env python3

# SPDX-License-Identifier: MPL-2.0

"""Validate linux-regression-exclusions.tsv and replace excluded tests with skip stubs."""

import os
import shlex
import sys
from pathlib import Path
from typing import NoReturn

HEADER = "path\treason\trestore_when"


def fail(msg: str) -> NoReturn:
    sys.exit(f"error: {msg}")


def load_exclusions(test_dir: Path, exclusions_tsv: Path) -> list[tuple[Path, str, str]]:
    if not test_dir.is_dir():
        fail(f"Missing regression test directory: {test_dir}")
    if not exclusions_tsv.is_file():
        fail(f"Missing exclusions file: {exclusions_tsv}")

    raw = exclusions_tsv.read_bytes().decode("utf-8")
    if "\r" in raw:
        fail("Exclusions file must use LF line endings")
    lines = raw.removesuffix("\n").split("\n") if raw else []
    if not lines or lines[0] != HEADER:
        fail("Invalid or missing exclusions header")

    seen: set[str] = set()
    entries: list[tuple[Path, str, str]] = []
    for lineno, line in enumerate(lines[1:], start=2):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 3 or any(not f.strip() for f in fields):
            fail(f"Invalid exclusion row {lineno}")
        rel_path, reason, _restore_when = fields

        parts = rel_path.split("/")
        if (
            rel_path != rel_path.strip()
            or len(parts) < 2
            or any(p in ("", ".", "..") for p in parts)
            or parts[-1] == "run_test.sh"
        ):
            fail(f"Invalid exclusion path at row {lineno}: {rel_path}")
        if rel_path in seen:
            fail(f"Duplicate exclusion at row {lineno}: {rel_path}")

        target = test_dir.joinpath(*parts)
        if not (target.is_file() and os.access(target, os.X_OK)):
            fail(f"Excluded path is not an executable file at row {lineno}: {rel_path}")

        seen.add(rel_path)
        entries.append((target, rel_path, reason))
    return entries


def install_skip_stub(target: Path, rel_path: str, reason: str) -> None:
    target.unlink()
    target.write_text(
        "#!/bin/sh\n"
        f"printf 'SKIP (linux-regression): %s - %s\\n' "
        f"{shlex.quote(rel_path)} {shlex.quote(reason)}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    target.chmod(0o755)


def main() -> None:
    if len(sys.argv) != 3:
        fail(f"Usage: {sys.argv[0]} <test_dir> <exclusions_tsv>")

    test_dir = Path(sys.argv[1]).resolve()
    exclusions_tsv = Path(sys.argv[2]).resolve()
    for target, rel_path, reason in load_exclusions(test_dir, exclusions_tsv):
        install_skip_stub(target, rel_path, reason)


if __name__ == "__main__":
    main()
