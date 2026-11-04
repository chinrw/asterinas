# SPDX-License-Identifier: MPL-2.0
"""Control one census window from the host without guest console traffic."""
import argparse
import json
import os
from pathlib import Path
import random
import secrets
import socket
import struct
import time

MAGIC = 0x41535443454E5355
VERSION = 1
BLOB_BYTES = 327808
REQUEST = struct.Struct('<6Q')
REPLY = struct.Struct('<14Q')
PING, CLOCK, START, FREEZE = range(4)
KEYS = ('magic', 'version', 'command', 'nonce', 'case_id', 'mode', 'status_errno',
        'tsc_before', 'tsc_after', 'frequency', 'epoch', 'dump_bytes', 'busy_retries', 'owner_pid')


def call(command, case_id=0, mode=0):
    nonce = secrets.randbits(64)
    host = os.environ.get('PERF_CLIENT_HOST', '10.0.2.15')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
        channel.settimeout(6 if command == FREEZE else 3)
        channel.connect((host, int(os.environ.get('CENSUS_PORT', '9997'))))
        before = time.monotonic_ns()
        channel.send(REQUEST.pack(MAGIC, VERSION, command, nonce, case_id, mode))
        packet = channel.recv(REPLY.size + 1)
        after = time.monotonic_ns()
    if len(packet) != REPLY.size:
        raise ValueError(f'census reply length {len(packet)}, expected {REPLY.size}')
    words = REPLY.unpack(packet)
    if words[:6] != (MAGIC, VERSION, command, nonce, case_id, mode):
        raise ValueError('census reply identity mismatch')
    result = dict(zip(KEYS, words))
    result.update(host_before_ns=before, host_after_ns=after, rtt_ns=after-before)
    if result['status_errno']:
        raise OSError(result['status_errno'], f'census command {command}: {result}')
    if not result['frequency'] or not result['owner_pid'] or result['tsc_before'] > result['tsc_after']:
        raise ValueError('invalid census clock or owner')
    if result['busy_retries'] > 2000:
        raise ValueError('unbounded census STOP retries')
    expected_bytes = BLOB_BYTES if command == FREEZE else 0
    if result['dump_bytes'] != expected_bytes:
        raise ValueError('invalid census dump byte count')
    if command in (START, FREEZE) and not result['epoch']:
        raise ValueError('missing census epoch')
    return result


def clock():
    samples = [call(CLOCK) for _ in range(3)]
    if len({(item['frequency'], item['owner_pid']) for item in samples}) != 1:
        raise ValueError('census clock identity changed')
    frequency = samples[0]['frequency']
    for item in samples:
        item['offset_lower_ns'] = item['host_before_ns'] - (item['tsc_after'] * 10**9 + frequency - 1) // frequency
        item['offset_upper_ns'] = item['host_after_ns'] - item['tsc_before'] * 10**9 // frequency
    low = max(item['offset_lower_ns'] for item in samples)
    high = min(item['offset_upper_ns'] for item in samples)
    if low > high:
        raise ValueError('inconsistent census clock calibration intervals')
    return dict(frequency=frequency, owner_pid=samples[0]['owner_pid'], offset_lower_ns=low,
                offset_upper_ns=high, samples=samples)


def atomic_json(path, record):
    temporary = path.with_suffix('.tmp')
    with temporary.open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')
    temporary.replace(path)


def plan(directory):
    if (directory / 'census-plan.json').exists() or (directory / 'census-plan.tsv').exists():
        raise FileExistsError('census plan already exists')
    seed = int(os.environ.get('CENSUS_ORDER_SEED', '20261104'))
    rng = random.Random(seed)
    workloads = ['idle', 'future', 'go'] + [f'micro_{n}_{operation}' for n in (1, 64, 4096)
                 for operation in ('cancel', 'drop', 'rearm', 'expiry')] + ['periodic', 'redis', 'redis_churn']
    known_workloads = workloads.copy()
    if os.environ.get('CENSUS_PREFLIGHT') == '1':
        workloads = ['idle', 'future', 'go', 'micro_1_expiry', 'micro_64_cancel', 'micro_4096_rearm', 'periodic', 'redis', 'redis_churn']
    if os.environ.get('CENSUS_CASES'):
        selected = os.environ['CENSUS_CASES'].split(',')
        if len(set(selected)) != len(selected) or any(item not in known_workloads for item in selected):
            raise ValueError('invalid CENSUS_CASES selection')
        workloads = selected
    enabled_modes = [int(value) for value in os.environ.get('CENSUS_MODES', '0,1,2').split(',')]
    if not enabled_modes or len(set(enabled_modes)) != len(enabled_modes) or any(value not in (0, 1, 2) for value in enabled_modes):
        raise ValueError('invalid CENSUS_MODES selection')
    go_runs = int(os.environ.get('CENSUS_GO_RUNS', '5'))
    if not 1 <= go_runs <= 100:
        raise ValueError('invalid CENSUS_GO_RUNS')
    rows = []
    for workload in workloads:
        modes = enabled_modes.copy()
        rng.shuffle(modes)
        for mode in modes:
            rows.append(dict(case_id=len(rows)+1, mode=mode, workload=workload))
    atomic_json(directory / 'census-plan.json', dict(seed=seed, windows=rows, go_repetitions=go_runs, plain=os.environ.get('CENSUS_PLAIN') == '1',
        boundary='Host START precedes phase dispatch; FREEZE follows validated completion. Console and shell dispatch overhead remain inside the window.'))
    with (directory / 'census-plan.tsv').open('x') as stream:
        for row in rows:
            stream.write(f"{row['case_id']}\t{row['mode']}\t{row['workload']}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('plan', 'ping', 'start', 'freeze'))
    parser.add_argument('case_id', type=int, nargs='?', default=0)
    parser.add_argument('mode', type=int, nargs='?', default=0)
    args = parser.parse_args()
    directory = Path(os.environ['PERF_SAMPLE_DIR'])
    if args.action == 'plan':
        plan(directory)
        return
    if args.action == 'ping':
        atomic_json(directory / 'census-ready.json', call(PING))
        return
    if args.case_id <= 0 or args.mode not in (0, 1, 2):
        raise ValueError('invalid census case or mode')
    path = directory / f'census-control-{args.case_id}.json'
    if args.action == 'start':
        if path.exists():
            raise FileExistsError(path)
        record = dict(case_id=args.case_id, mode=args.mode, complete=False, clock_before=clock())
        # A timed-out START has an unknown outcome; it must never be retried.
        try:
            record['start'] = call(START, args.case_id, args.mode)
            if record['start']['owner_pid'] != record['clock_before']['owner_pid']:
                raise ValueError('census owner changed at START')
        finally:
            atomic_json(path, record)
    else:
        record = json.loads(path.read_text())
        if record['case_id'] != args.case_id or record['mode'] != args.mode or record['complete']:
            raise ValueError('invalid or already frozen census window')
        try:
            record['freeze'] = call(FREEZE, args.case_id, args.mode)
            record['clock_after'] = clock()
            start, freeze = record['start'], record['freeze']
            if (start['epoch'], start['owner_pid'], start['frequency']) != (freeze['epoch'], freeze['owner_pid'], freeze['frequency']):
                raise ValueError('census epoch, owner, or frequency changed')
            if freeze['owner_pid'] != record['clock_after']['owner_pid']:
                raise ValueError('census owner changed after FREEZE')
            if start['frequency'] != record['clock_before']['frequency'] or freeze['frequency'] != record['clock_after']['frequency']:
                raise ValueError('census clock frequency changed')
            record['complete'] = True
        finally:
            atomic_json(path, record)
    print('CENSUS_CONTROL ' + json.dumps(dict(action=args.action, case_id=args.case_id, mode=args.mode, complete=record['complete'])), flush=True)


if __name__ == '__main__':
    main()
