# SPDX-License-Identifier: MPL-2.0
"""The supplied 3s/fresh-guest protocol with strict records and scoped cleanup."""
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import time

SUITE = Path(__file__).resolve().parent
REPO = SUITE.parents[3]
OUT = REPO / "target/notingo-native-controls/fresh-benchmark"
VARIANTS = ("base", "retirement")

def validate_record(record, sample, digest, owner):
    pid, ticks, sample_id = owner.read_text().split()
    for key, expected in [("sample_id", sample["id"]), ("helper", sample["helper"]),
                          ("helper_sha256", digest), ("qemu_pid", int(pid)),
                          ("qemu_start_ticks", int(ticks)), ("deadline_ms", 3000),
                          ("endpoint", "command_exit_and_barrier")]:
        if record.get(key) != expected:
            raise ValueError(f"invalid result field: {key}")
    if int(sample_id) != sample["id"] or type(record.get("elapsed_ns")) is not int:
        raise ValueError("invalid sample identity or timing")
    if record["elapsed_ns"] < 0:
        raise ValueError("negative timing")
    allowed = {"pass", "deadline", "deadline_race", "helper_failure", "guest_error", "watchdog_error"}
    if record.get("outcome") not in allowed:
        raise ValueError("unknown outcome")
    if record["outcome"] == "pass" and record["elapsed_ns"] > 3_000_000_000:
        raise ValueError("late PASS")
    return record

def run_sample(sample, digests):
    directory = OUT / f"sample-{sample['id']:03d}-{sample['variant']}-{sample['helper']}"
    directory.mkdir()
    marker = directory / "result.json"
    owner = directory / "qemu.owner"
    exit_file = directory / "driver.exit"
    image = REPO / "target/nixos/asterinas.img"
    subprocess.run(["cp", "--reflink=auto", "--sparse=always",
                    str(OUT / sample["variant"] / "clean.img"), str(image)], check=True)
    environment = dict(os.environ, PROBE_HOST_SAMPLE=sample["helper"],
                       PROBE_HOST_MARKER=str(marker), PROBE_SAMPLE_ID=str(sample["id"]),
                       PROBE_HELPER_SHA256=digests[sample["helper"]], PROBE_QEMU_OWNER=str(owner),
                       PROBE_QEMU_CONTROL=str(SUITE / "owned_qemu.py"),
                       QEMU_BIN=str(SUITE / "owned_qemu.py"), SMP="1", MEM="8G", QEMU_DISPLAY="none")
    for key in ("GODEBUG", "GOTRACEBACK"):
        environment.pop(key, None)
    # Keep the session leader alive until cleanup, so its process-group identity stays owned.
    command = ['bash', '-c', 'make run_nixos NIXOS_TEST_SUITE=development-tools-go NIXOS_TEST_TIMEOUT=60s NIXOS_TEST_CASE=notingo_host_sample; code=$?; printf "%s\\n" "$code" > "$1"; exec sleep 86400', 'benchmark-supervisor', str(exit_file)]
    started = time.monotonic()
    record = {**sample, "outcome": "infrastructure", "reason": "outer_timeout"}
    cleanup_ok = True
    with (directory / "driver.log").open('w') as log:
        process = subprocess.Popen(command, cwd=REPO, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while time.monotonic() - started < 180:
                if marker.exists():
                    try:
                        measured = validate_record(json.loads(marker.read_text()), sample, digests[sample['helper']], owner)
                        record = {**sample, **measured}
                    except (ValueError, OSError) as error:
                        record['reason'] = str(error)
                    break
                if exit_file.exists() or process.poll() is not None:
                    record['reason'] = 'driver_exited_without_valid_record'
                    break
                time.sleep(0.05)
        finally:
            if owner.exists():
                stopped = subprocess.run(['python3', str(SUITE / 'owned_qemu.py'), '--stop', str(owner)], capture_output=True, text=True)
                cleanup_ok = stopped.returncode == 0
                (directory / 'cleanup.log').write_text(stopped.stdout + stopped.stderr)
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        record['driver_exit'] = process.returncode
    record['sample_started'] = Path(str(marker)+'.started').exists()
    if record['outcome'] == 'infrastructure' and record['sample_started']:
        record['outcome'] = 'started_without_record'
    record['cleanup_ok'] = cleanup_ok
    record['host_total_seconds'] = time.monotonic() - started
    record['host_loadavg'] = Path('/proc/loadavg').read_text().strip()
    for name in ('qemu.log', 'qemu-serial.log'):
        path = REPO / name
        if path.exists():
            path.replace(directory / name)
    (directory / 'record.json').write_text(json.dumps(record, indent=2)+'\n')
    print('FRESH_SAMPLE ' + json.dumps(record, sort_keys=True), flush=True)
    if not cleanup_ok:
        raise RuntimeError('Cannot safely start the next sample after failed cleanup')
    return record

def main():
    digests = json.loads((OUT / 'helper-sha256.json').read_text())
    rng = random.Random(20261028)
    plan = []
    for block in range(25):
        variants = list(VARIANTS)
        rng.shuffle(variants)
        for variant in variants:
            plan.append({'id':len(plan),'block':block,'variant':variant,'helper':'minimal'})
    for helper in ('sleep20', 'sleep1000'):
        for variant in VARIANTS:
            plan.append({'id':len(plan),'block':25,'variant':variant,'helper':helper})
    (OUT / 'plan.json').write_text(json.dumps({'seed':20261028,'samples':plan},indent=2)+'\n')
    rows = []
    for sample in plan:
        row = run_sample(sample, digests)
        rows.append(row)
        with (OUT / 'samples.jsonl').open('a') as stream:
            stream.write(json.dumps(row,sort_keys=True)+'\n')
    summary = {}
    for variant in VARIANTS:
        minimal = [r for r in rows if r['variant']==variant and r['helper']=='minimal']
        controls = [r for r in rows if r['variant']==variant and r['helper']!='minimal']
        summary[variant]={'minimal_attempts':len(minimal),'outcomes':{k:sum(r['outcome']==k for r in minimal) for k in sorted({r['outcome'] for r in minimal})},'controls':[{'helper':r['helper'],'outcome':r['outcome']} for r in controls]}
    valid = all(r['outcome'] not in {'infrastructure','started_without_record','deadline_race','watchdog_error'} for r in rows)
    fixed = all(r['outcome']=='pass' for r in rows if r['variant']=='retirement')
    controls = all(r['outcome']=='pass' for r in rows if r['helper']!='minimal')
    reproduced = any(r['outcome']=='deadline' for r in rows if r['variant']=='base' and r['helper']=='minimal')
    result={'protocol':'3s host command exit and barrier; fresh guest per sample','seed':20261028,'variants':summary,'valid_records':valid,'candidate_passed':fixed,'controls_passed':controls,'baseline_deadline_reproduced':reproduced}
    (OUT/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print('FRESH_SUMMARY '+json.dumps(result,sort_keys=True),flush=True)
    return 0 if valid and fixed and controls and reproduced else 1

if __name__ == '__main__':
    raise SystemExit(main())
