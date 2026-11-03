#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Early same-tool, four-vCPU, 35-second profile gate before kernel builds."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import re
import subprocess
import time
from guest_profile import GuestProfile, task_identity
from perf_data import parse
from profile_validation import validate_vcpu_profile


def poll_wakeup_evidence(log):
    counts = re.findall(r'\[ perf record: Woken up (\d+) times to write data \]', log)
    if len(counts) != 1:
        raise ValueError('Missing or ambiguous recorder poll-wakeup count')
    count = int(counts[0])
    return {'poll_wakeups': count, 'poll_wakeup_limit': 100000,
            'poll_quality_valid': count <= 100000,
            'poll_quality_scope': 'Diagnostic gross-spin guard; passing does not prove profiling neutrality.'}


def terminate(_signum, _frame):
    raise SystemExit(143)


def main():
    # This is the existing, corrected capability fixture already in the suite.
    from perf_capability import boot_sector, MARKER, stop as stop_qemu
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    perf = os.environ['TAIL_PERF_BIN']
    result = {'valid': False, 'duration_seconds': 35, 'same_perf_binary': perf,
              'scope': 'Owned four-vCPU boot fixture; vCPU-only sampling, no workload or kernel instrumentation.'}
    profile = None
    qemu = None
    log = None
    signal.signal(signal.SIGTERM, terminate)
    try:
        sector, rip = boot_sector()
        result['expected_loop_rip'] = hex(rip)
        (out / 'boot.img').write_bytes(sector)
        qmp = out / 'qemu.qmp'
        if len(str(qmp).encode()) >= 104:
            raise ValueError('Owned QMP socket path is too long')
        command = [shutil.which('qemu-system-x86_64') or 'qemu-system-x86_64',
            '-accel', 'kvm', '-cpu', 'host', '-smp', '4', '-m', '64M',
            '-drive', f'file={out / "boot.img"},format=raw,snapshot=on,if=ide', '-boot', 'c',
            '-nic', 'none', '-display', 'none', '-serial', 'none', '-monitor', 'none',
            '-debugcon', f'file:{out / "debugcon.log"}', '-global', 'isa-debugcon.iobase=0xe9',
            '-qmp', f'unix:{qmp},server=on,wait=off', '-no-reboot', '-no-shutdown']
        (out / 'qemu.command.json').write_text(json.dumps(command) + '\n')
        for name, argv in [('perf-version', [perf, '--version']), ('qemu-version', [command[0], '--version'])]:
            version = subprocess.run(argv, capture_output=True, text=True, timeout=5)
            (out / (name + '.json')).write_text(json.dumps({'command': argv, 'exit_code': version.returncode,
                'stdout': version.stdout, 'stderr': version.stderr}) + '\n')
            if version.returncode:
                raise RuntimeError(f'{name} failed')
        log = (out / 'qemu.log').open('wb')
        qemu = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        identity = task_identity(qemu.pid, qemu.pid)
        owner = out / 'qemu.owner'
        owner.write_text(f'{qemu.pid} {identity["start_ticks"]} fixture\n')
        profile = GuestProfile(out, perf)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if qemu.poll() is not None:
                raise RuntimeError('Fixture QEMU exited before profile readiness')
            profile.poll_start(owner)
            marker = out / 'debugcon.log'
            if profile.proc is not None and marker.exists() and MARKER in marker.read_bytes():
                break
            if profile.meta['errors']:
                raise RuntimeError(f'Profiler start failed: {profile.meta["errors"]}')
            time.sleep(.05)
        else:
            raise RuntimeError('Fixture readiness exceeded 30 seconds')
        result['measurement_started_ns'] = time.monotonic_ns()
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            profile.poll()
            if qemu.poll() is not None or profile.meta['unexpected_early_exit']:
                raise RuntimeError('QEMU or profiler exited during 35-second fixture')
            time.sleep(.05)
        result['measurement_finished_ns'] = time.monotonic_ns()
        metadata = profile.stop()
        data = parse(out / 'guest-perf.data')
        loop = [row for row in data['samples'] if row['context'] in (4, 5) and int(row['ip'], 16) == rip]
        if len(loop) < 3:
            raise RuntimeError('Expected actual guest loop RIP not established')
        window = {'name': 'boot-fixture-recording', 'clock': 'CLOCK_MONOTONIC',
            'start_ns': max(metadata['spawned_ns'], min(row['time_ns'] for row in data['samples'])),
            'end_ns': metadata['stop_requested_ns'] - 1}
        perf_log = (out / 'guest-perf.log').read_text(errors='replace')
        result.update(poll_wakeup_evidence(perf_log))
        if not result['poll_quality_valid']:
            raise RuntimeError('Gross recorder poll spin exceeds diagnostic quality limit')
        validation = validate_vcpu_profile(data, metadata, [window], [[hex(rip), hex(rip + 2)]], perf_log)
        result.update(validation=validation, loop_samples=len(loop), raw_inherit=data['inherit'],
                      target_threads=metadata['target_threads'], valid=validation['valid'])
        if not result['valid']:
            raise RuntimeError('Fixture profile validation failed')
    except Exception as error:
        result.update(valid=False, error=f'{type(error).__name__}: {error}')
    finally:
        # Always stop the recorder before the owned VM, even on readiness failure.
        try:
            if profile is not None and 'stop_finished_ns' not in profile.meta:
                result['profiler_stop'] = profile.stop()
        finally:
            try:
                if qemu is not None:
                    stop_qemu(qemu)
                    result['owned_qemu_cleanup_ok'] = qemu.returncode is not None
            except Exception as error:
                result.update(valid=False, cleanup_error=f'{type(error).__name__}: {error}')
            finally:
                if log is not None:
                    log.close()
                if not result.get('owned_qemu_cleanup_ok'):
                    result['valid'] = False
                for path in out.iterdir():
                    if path.is_file() and not path.is_symlink():
                        path.chmod(path.stat().st_mode | 0o444)
                (out / 'fixture-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
