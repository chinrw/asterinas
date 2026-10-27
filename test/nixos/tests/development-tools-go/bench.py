# SPDX-License-Identifier: MPL-2.0
"""Paired startup-stall benchmark for the minimal cgo helpers.

Each sample starts a fresh process.  The helper is killed (SIGKILL, no
goroutine dump) after the timeout, so a failure is recorded as a timeout
instead of contaminating the result with a multi-second console dump.
"""

import json
import os
import random
import statistics
import subprocess
import time

SEED = 20261027
TIMEOUT_S = 3.0
VARIANTS = [
    ("minimal", 50),
    ("sleep20", 30),
    ("sleep1000", 30),
]

random.seed(SEED)
plan = []
for name, count in VARIANTS:
    plan.extend([name] * count)
random.shuffle(plan)

results = {
    name: {"attempts": 0, "passes": 0, "failures": 0, "times": []}
    for name, _ in VARIANTS
}
environment = dict(os.environ, GOMAXPROCS="1")

for index, name in enumerate(plan, 1):
    path = f"/etc/notingo/{name}"
    started = time.monotonic()
    passed = False
    try:
        completed = subprocess.run(
            ["timeout", "-s", "KILL", f"{TIMEOUT_S:g}s", path, "poll"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=TIMEOUT_S + 2.0,
        )
        elapsed = time.monotonic() - started
        passed = completed.returncode == 0 and "BARRIER_OK" in completed.stdout
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started

    result = results[name]
    result["attempts"] += 1
    if passed:
        result["passes"] += 1
        result["times"].append(elapsed)
    else:
        result["failures"] += 1
    print(
        f"BENCH_RUN {index} {name} {'OK' if passed else 'FAIL'} {elapsed:.6f}",
        flush=True,
    )

summary = {"seed": SEED, "timeout_s": TIMEOUT_S, "variants": {}}
for name, _ in VARIANTS:
    result = results[name]
    times = sorted(result["times"])
    if times:
        p95_index = min(len(times) - 1, int(0.95 * (len(times) - 1)))
        latency = {
            "min_s": times[0],
            "median_s": statistics.median(times),
            "p95_s": times[p95_index],
            "max_s": times[-1],
        }
    else:
        latency = {"min_s": None, "median_s": None, "p95_s": None, "max_s": None}
    summary["variants"][name] = {
        "attempts": result["attempts"],
        "passes": result["passes"],
        "failures": result["failures"],
        **latency,
    }

print("BENCH_JSON " + json.dumps(summary, sort_keys=True), flush=True)
print("BENCH_DONE", flush=True)
