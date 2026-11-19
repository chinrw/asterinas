#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Assign workloads to shards and run one QEMU guest per selected subsystem."""

import argparse
from collections import Counter, defaultdict
import csv
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


def shard_owners(cases, shards, mode):
    if mode == "case":
        offsets = Counter()
        for case in cases:
            action = case["action"]
            yield offsets[action] % shards
            offsets[action] += 1
        return

    counts = Counter({case["group"]: 0 for case in cases})
    counts.update(case["group"] for case in cases if case["action"] == "RUN")
    loads = [0] * shards
    owners = {}
    for group in sorted(counts, key=lambda name: (-counts[name], name)):
        owner = min(range(shards), key=loads.__getitem__)
        owners[group] = owner
        loads[owner] += counts[group]
    for case in cases:
        yield owners[case["group"]]


def monitor(command, console, control, timeout):
    """Allow for guest startup, PTY cleanup and flushing after the per-case watchdog."""
    last_size = 0
    grace = 15
    deadline = time.monotonic() + timeout + grace
    with console.open("wb") as log:
        vm = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            while True:
                try:
                    return vm.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    size = control.stat().st_size if control.exists() else 0
                    if size != last_size:
                        last_size = size
                        deadline = time.monotonic() + timeout + grace
                    if time.monotonic() >= deadline:
                        return 124
        finally:
            if vm.poll() is None:
                os.killpg(vm.pid, signal.SIGTERM)
                try:
                    vm.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(vm.pid, signal.SIGKILL)
                    vm.wait()


def split_console(console, group, numbers):
    """Retain the full console and index the workload output by its guest markers."""
    starts = Counter()
    markers = []
    target = None
    active = None
    try:
        with console.open("rb") as source:
            for raw in source:
                line = raw.rstrip(b"\r\n").decode(errors="replace")
                if match := re.fullmatch(rf"BATCH_BEGIN {group} (\d+) [\d.]+", line):
                    if target:
                        target.close()
                    markers.append(line)
                    active = match[1] if match[1] in numbers else None
                    target = (
                        (console.parent / f"{active}.log").open("ab")
                        if active
                        else None
                    )
                if target:
                    target.write(raw)
                    if line == f"REGRESSION_CASE_START {group} {active}":
                        starts[active] += 1
                if line.startswith("BATCH_END "):
                    markers.append(line)
                    if target:
                        target.close()
                        target = None
    finally:
        if target:
            target.close()
    return markers, starts


def collect_group(group, cases, folder, qemu_status, results):
    numbers = {case["case"] for case in cases}
    markers, starts = split_console(folder / "guest.log", group, numbers)
    control = folder / "guest.result"
    records = (
        control.read_text(errors="replace").splitlines() if control.exists() else []
    )
    pos = 0
    failed = False
    guest_failed = False
    for case in cases:
        number = case["case"]
        began = starts.get(number, 0)
        status = "FAIL" if began else "NOT_RUN"
        reason = (
            "Missing workload completion" if began else "Guest did not start workload"
        )
        code, duration = "missing", 0
        pair = records[pos : pos + 2]
        timestamp = r"(\d+(?:\.\d+)?)"
        if len(pair) == 2:
            first = re.fullmatch(rf"BATCH_BEGIN {group} {number} {timestamp}", pair[0])
            last = re.fullmatch(
                rf"BATCH_END {group} {number} (\d+) {timestamp}", pair[1]
            )
            if (
                first
                and last
                and markers[pos : pos + 2] == pair
                and float(last[2]) >= float(first[1])
            ):
                code = last[1]
                duration = round(float(last[2]) - float(first[1]), 2)
                pos += 2
                guest_failed |= code != "0"
                if began == 1:
                    status, reason = (
                        ("PASS", "") if code == "0" else ("FAIL", "workload failed")
                    )
                elif began:
                    reason = "Duplicate workload start"
        failed |= status != "PASS"
        results.writerow(
            [
                group,
                number,
                status,
                code,
                qemu_status,
                duration,
                case["command"],
                reason,
            ]
        )
        print(f"{group}/{number}: {status} ({duration}s)", flush=True)

    terminal = f"LINUX_REGRESSION_RESULT {group} {cases[0]['case']} {int(guest_failed)}"
    error = None
    if (
        qemu_status != 0
        or pos != 2 * len(cases)
        or len(markers) != pos
        or records[pos:] != [terminal]
    ):
        error = (
            f"{group}: incomplete or invalid guest session (QEMU status {qemu_status})"
        )
    return failed, error


def run_groups(cases, directory, timeout, command):
    groups = defaultdict(list)
    for case in cases:
        if case["action"] == "RUN":
            groups[case["group"]].append(case)

    failed = False
    (directory / "guest-times.tsv").write_text("group\tworkloads\twall_seconds\n")
    with (directory / "results.tsv").open("a", newline="") as source:
        results = csv.writer(
            source,
            delimiter="\t",
            quotechar=None,
            quoting=csv.QUOTE_NONE,
            lineterminator="\n",
        )
        for group, members in groups.items():
            folder = directory / group
            folder.mkdir(exist_ok=True)
            numbers = ",".join(case["case"] for case in members)
            params = (
                f"regression_group={group} regression_case={members[0]['case']} "
                f"regression_batch={numbers} regression_timeout={timeout}"
            )
            argv = command.copy()
            idx = argv.index("-append") + 1
            argv[idx] += " " + params
            if len(argv[idx].encode()) >= 2048:
                raise ValueError(
                    f"{group}: selected cases exceed the x86 kernel command line limit"
                )
            control = folder / "guest.result"
            argv += ["-serial", f"file:{control}"]
            print(f"Booting Linux for {group} ({len(members)} workloads)", flush=True)
            started = time.monotonic()
            interrupted = False
            try:
                rc = monitor(argv, folder / "guest.log", control, timeout)
            except KeyboardInterrupt:
                rc = 130
                interrupted = True
            bad, error = collect_group(group, members, folder, rc, results)
            source.flush()
            if error:
                (folder / "guest.error").write_text(error + "\n")
                print(error, file=sys.stderr)
            elapsed = time.monotonic() - started
            with (directory / "guest-times.tsv").open("a") as times:
                times.write(f"{group}\t{len(members)}\t{elapsed:.3f}\n")
            print(f"{group} finished in {elapsed:.2f}s", flush=True)
            failed |= bad or bool(error)
            if interrupted:
                return 130
    return int(failed)


def interrupt(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    select = actions.add_parser("select")
    select.add_argument("manifest", type=Path)
    select.add_argument("mode", choices=("subsystem", "case"))
    select.add_argument("shards", type=int)
    select.add_argument("index", type=int)
    run = actions.add_parser("run")
    run.add_argument("manifest", type=Path)
    run.add_argument("directory", type=Path)
    run.add_argument("timeout", type=int)
    run.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    with args.manifest.open(newline="") as source:
        cases = list(csv.DictReader(source, delimiter="\t", quoting=csv.QUOTE_NONE))
    if args.action == "select":
        writer = csv.writer(
            sys.stdout,
            delimiter="\t",
            quotechar=None,
            quoting=csv.QUOTE_NONE,
            lineterminator="\n",
        )
        for row, owner in zip(cases, shard_owners(cases, args.shards, args.mode)):
            if owner == args.index:
                writer.writerow(row.values())
    else:
        signal.signal(signal.SIGTERM, interrupt)
        sys.exit(run_groups(cases, args.directory, args.timeout, args.command))
