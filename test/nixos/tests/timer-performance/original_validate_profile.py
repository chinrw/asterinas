# SPDX-License-Identifier: MPL-2.0
"""Validate profile coverage using explicit host CLOCK_MONOTONIC windows."""
import argparse
import collections
import json
from pathlib import Path
from perf_data import parse


def validate(data, metadata, windows, kernel_ranges=None):
    errors = []
    samples = data['samples']
    guests = [row for row in samples if row['context'] in (4, 5)]
    if not data['use_clockid'] or data['clockid'] != 1 or metadata.get('clock') != 'CLOCK_MONOTONIC':
        errors.append('CLOCK_MONOTONIC is not established in both attr and metadata')
    if data['lost_events'] or data['lost_samples']:
        errors.append('Perf reports lost events or samples')
    if metadata.get('unexpected_early_exit') or metadata.get('exit_code') != 0:
        errors.append('Recorder exited early or unsuccessfully')
    if metadata.get('errors'):
        errors.append('Recorder metadata contains errors')
    if metadata.get('qemu_identity_matches_at_stop') is False:
        errors.append('QEMU identity changed during recording')
    if not guests:
        errors.append('No actual guest-context instruction-pointer samples')
    qemu = metadata.get('qemu', {})
    foreign = [row for row in samples if row['pid'] != qemu.get('pid')]
    if foreign:
        errors.append('Sample PID differs from the owned QEMU identity')
    first_sample = min((row['time_ns'] for row in samples), default=None)
    window_results = []
    if not windows:
        errors.append('No requested host-monotonic windows supplied')
    for window in windows:
        start, end = window['start_ns'], window['end_ns']
        if window.get('clock') != 'CLOCK_MONOTONIC' or start >= end:
            raise ValueError('Invalid window or clock; host monotonic nanoseconds required')
        # A spawn timestamp alone does not prove perf had enabled its events.
        covered = (metadata.get('spawned_ns', end + 1) <= start
                   and first_sample is not None and first_sample <= start
                   and metadata.get('stop_requested_ns', 0) >= end
                   and not metadata.get('unexpected_early_exit', True))
        if not covered:
            errors.append(f'Recorder coverage not established for {window["name"]}')
        rows = [row for row in guests if start <= row['time_ns'] <= end]
        window_results.append({'name': window['name'], 'coverage_established': covered,
            'guest_samples': len(rows), 'guest_kernel_samples': sum(row['context'] == 4 for row in rows),
            'samples_by_host_cpu': dict(collections.Counter(row['cpu'] for row in rows)),
            'samples_by_qemu_tid': dict(collections.Counter(row['tid'] for row in rows))})
    mapped = None
    if kernel_ranges is not None:
        ranges = [(int(lo, 0), int(hi, 0)) for lo, hi in kernel_ranges]
        if not ranges or any(lo >= hi for lo, hi in ranges):
            raise ValueError('Invalid kernel executable address ranges')
        mapped = sum(row['context'] == 4 and any(lo <= int(row['ip'], 16) < hi for lo, hi in ranges)
                     for row in guests)
        if not mapped:
            errors.append('No global guest-kernel sample maps to supplied executable ranges')
    return {'valid': not errors, 'errors': errors, 'guest_samples': len(guests),
            'guest_kernel_mapped_samples': mapped, 'windows': window_results,
            'loss': {key: data[key] for key in ('lost_records', 'lost_events', 'lost_sample_records', 'lost_samples')},
            'interpretation': 'Sparse window samples and absent host-CPU samples do not establish absent execution.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('data')
    parser.add_argument('metadata')
    parser.add_argument('windows')
    parser.add_argument('--kernel-ranges')
    args = parser.parse_args()
    report = validate(parse(args.data), json.loads(Path(args.metadata).read_text()),
                      json.loads(Path(args.windows).read_text()),
                      json.loads(Path(args.kernel_ranges).read_text()) if args.kernel_ranges else None)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['valid'] else 1)
