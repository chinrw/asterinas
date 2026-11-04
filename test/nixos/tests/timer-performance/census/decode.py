# SPDX-License-Identifier: MPL-2.0
"""Recover serial snapshots and validate them against the frozen census ABI."""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import struct

ABI_PATH = Path(__file__).with_name('abi.json')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_row(row, mode):
    require(all(not value for name, value in row.items() if name.startswith('reserved_')), 'nonzero reserved field')
    if mode == 0:
        require(not any(row.values()), 'gate-off mode recorded counters')
        return
    if mode == 1:
        require(all(not value for name, value in row.items() if '_tsc_' in name), 'counter-only mode recorded timing')
    require(row['dispatch_calls'] == row['dispatch_empty'] + row['dispatch_nonempty'], 'dispatch partition mismatch')
    require(row['min_probe'] == row['due_pop'] + row['cancelled_pop'] + row['dispatch_nonempty'], 'minimum probe identity mismatch')
    require(row['future_break'] <= row['dispatch_nonempty'], 'too many future breaks')
    require(row['due_pop'] == row['callback_invoked'] + row['callback_skip_cancel'] + row['callback_owner_gone'], 'callback outcome partition mismatch')
    require(row['periodic_rearm'] <= row['callback_invoked'], 'periodic rearm exceeds invoked callbacks')
    if 'periodic_retire_intent' in row:
        require(row['periodic_retire_intent'] == row['periodic_retire_hit'] + row['periodic_retire_miss'] + row['periodic_weak_upgrade_fail'], 'periodic retirement partition mismatch')
        for periodic, total in (('periodic_retire_intent', 'cancel_rearm'), ('periodic_retire_hit', 'retire_hit'), ('periodic_retire_miss', 'retire_miss'), ('periodic_weak_upgrade_fail', 'weak_upgrade_fail')):
            require(row[periodic] <= row[total], f'periodic subset exceeds total: {periodic}')
    require(row['cancel_explicit'] + row['cancel_rearm'] + row['cancel_owner_drop'] ==
            row['weak_upgrade_fail'] + row['retire_hit'] + row['retire_miss'], 'retirement intent partition mismatch')
    require(row['queue_observations'] == sum(row[f'queue_occupancy_bucket_{index}'] for index in range(8)), 'occupancy histogram mismatch')
    require(row['batch_observations'] == row['dispatch_calls'], 'batch observation mismatch')
    require(row['batch_observations'] == sum(row[f'batch_size_bucket_{index}'] for index in range(8)), 'batch histogram mismatch')
    require(row['batch_size_sum'] == row['due_pop'], 'batch sum differs from selected callbacks')
    require(row['queue_occupancy_max'] <= row['queue_occupancy_sum'], 'invalid occupancy maximum')
    require(row['batch_size_max'] <= row['batch_size_sum'], 'invalid batch maximum')
    for name in row:
        if name.endswith('_tsc_max'):
            require(row[name] <= row[name[:-3] + 'sum'], f'invalid timing maximum: {name}')


def aggregate(rows, names):
    result = dict.fromkeys(names, 0)
    for row in rows:
        for name, value in row['fields'].items():
            result[name] = max(result[name], value) if name.endswith('_max') else result[name] + value
    return result


def decode(data, abi, cpus=None):
    require(len(data) == abi['blob_bytes'], 'snapshot length mismatch')
    words = struct.unpack('<' + 'Q' * (len(data) // 8), data)
    hwords, kinds, fields = abi['hwords'], abi['kinds'], abi['fields_per_row']
    require(words[:6] == (abi['magic'], abi['version'], hwords, abi['cpus'], kinds, fields), 'snapshot ABI identity mismatch')
    header = dict(zip(abi['header'], words[:hwords]))
    require(header['tsc_freq_hz'] > 0 and header['epoch'] > 0 and header['case'] > 0, 'missing snapshot identity')
    require(header['mode'] in (0, 1, 2), 'invalid snapshot mode')
    require(header['start_tsc'] <= header['freeze_tsc'] <= header['quiescent_tsc'], 'snapshot timestamps reversed')
    require(not header['active'] and not header['overflow_cpu'] and not header['reserved'], 'snapshot is not quiescent and complete')
    names = [field['name'] for field in abi['fields']]
    require([field['index'] for field in abi['fields']] == list(range(fields)) and len(set(names)) == fields, 'invalid ABI field table')
    rows = []
    for cpu in range(abi['cpus']):
        for kind in range(kinds):
            offset = hwords + (cpu * kinds + kind) * fields
            values = words[offset:offset+fields]
            if cpus is not None and cpu >= cpus:
                require(not any(values), f'out-of-topology CPU row {cpu}')
            row = dict(zip(names, values))
            try:
                validate_row(row, header['mode'])
            except ValueError as error:
                raise ValueError(f'case={header["case"]} cpu={cpu} kind={kind}: {error}') from error
            if any(values):
                rows.append(dict(cpu=cpu, kind=kind, kind_name=abi['kind_names'][kind], fields=row))
    by_kind = {name: aggregate([row for row in rows if row['kind_name'] == name], names) for name in abi['kind_names']}
    totals = aggregate(rows, names)
    return dict(header=header, sha256=hashlib.sha256(data).hexdigest(), rows=rows, by_kind=by_kind, totals=totals,
                timing_clean=not totals['tsc_backwards'],
                scope='Rows identify execution CPU and manager kind, not individual managers. Occupancies are observation-weighted. Elapsed TSC sums overlap and do not measure CPU utilization. Window boundaries do not expose N_start/N_end for a manager balance equation.')


def recover(log):
    records = {}
    for line in log.splitlines():
        line = line.strip()
        match = re.fullmatch(r'CENSUS_BLOB_BEGIN (\d+) (\d+) ([a-f0-9]{64}) (\d+) ([a-f0-9]{64}) (\d+)', line)
        if match:
            case_id, raw_size, raw_hash, compressed_size, compressed_hash, chunks = match.groups()
            case_id, raw_size, compressed_size, chunks = map(int, (case_id, raw_size, compressed_size, chunks))
            require(case_id > 0 and case_id not in records, f'duplicate snapshot begin: {case_id}')
            require(raw_size == 327808 and 0 < compressed_size <= raw_size*2 and 0 < chunks <= 2048, 'invalid export dimensions')
            records[case_id] = dict(raw_size=raw_size, raw_hash=raw_hash, compressed_size=compressed_size,
                                    compressed_hash=compressed_hash, chunks=chunks, parts={}, ended=False)
            continue
        match = re.fullmatch(r'CENSUS_BLOB_CHUNK (\d+) (\d+) ([A-Za-z0-9+/=]+)', line)
        if match:
            case_id, index = map(int, match.group(1, 2))
            require(case_id in records, 'snapshot chunk precedes begin')
            record = records[case_id]
            require(not record['ended'] and 1 <= index <= record['chunks'] and index not in record['parts'], 'duplicate or out-of-range snapshot chunk')
            record['parts'][index] = match[3]
            continue
        match = re.fullmatch(r'CENSUS_BLOB_END (\d+)', line)
        if match:
            case_id = int(match[1])
            require(case_id in records and not records[case_id]['ended'], 'duplicate or unknown snapshot end')
            records[case_id]['ended'] = True
    recovered = {}
    for case_id, record in records.items():
        require(record['ended'] and len(record['parts']) == record['chunks'], f'incomplete export for case {case_id}')
        compressed = base64.b64decode(''.join(record['parts'][index] for index in range(1, record['chunks']+1)), validate=True)
        require(len(compressed) == record['compressed_size'] and hashlib.sha256(compressed).hexdigest() == record['compressed_hash'], 'compressed snapshot hash mismatch')
        raw = gzip.decompress(compressed)
        require(len(raw) == record['raw_size'] and hashlib.sha256(raw).hexdigest() == record['raw_hash'], 'raw snapshot hash mismatch')
        recovered[case_id] = raw
    return recovered


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('log', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--sample-dir', type=Path)
    parser.add_argument('--cpus', type=int, choices=(1, 4))
    args = parser.parse_args()
    abi = json.loads(ABI_PATH.read_text())
    raw_cases = recover(args.log.read_text(errors='replace'))
    require(raw_cases, 'no census snapshots in serial log')
    if args.sample_dir:
        plan = json.loads((args.sample_dir / 'census-plan.json').read_text())
        require(not plan['plain'], 'plain run unexpectedly exported census snapshots')
        require(set(raw_cases) == {row['case_id'] for row in plan['windows']}, 'exported case set differs from plan')
    else:
        plan = None
    decoded = {}
    for case_id, raw in raw_cases.items():
        result = decode(raw, abi, args.cpus)
        require(result['header']['case'] == case_id, 'serial case differs from blob case')
        if args.sample_dir:
            control = json.loads((args.sample_dir / f'census-control-{case_id}.json').read_text())
            require(control['complete'], 'incomplete census control record')
            start, freeze = control['start'], control['freeze']
            h = result['header']
            require((h['epoch'], h['mode'], h['tsc_freq_hz']) == (start['epoch'], start['mode'], start['frequency']), 'control/blob identity mismatch')
            require(freeze['epoch'] == h['epoch'] and freeze['dump_bytes'] == len(raw), 'freeze/blob identity mismatch')
            planned = next(row for row in plan['windows'] if row['case_id'] == case_id)
            require(planned['mode'] == h['mode'], 'plan/blob mode mismatch')
            result['workload'] = planned['workload']
        decoded[case_id] = result
    args.output.mkdir()
    for case_id, result in decoded.items():
        (args.output / f'census-{case_id}.bin').write_bytes(raw_cases[case_id])
        (args.output / f'census-{case_id}.json').write_text(json.dumps(result, indent=2) + '\n')
    timing_clean = all(result['timing_clean'] for result in decoded.values())
    (args.output / 'summary.json').write_text(json.dumps(dict(cases=len(decoded),
        validation_passed=timing_clean, timing_clean=timing_clean,
        cases_with_backward_tsc=[case_id for case_id, result in decoded.items() if not result['timing_clean']]), indent=2) + '\n')
    print(f'CENSUS_DECODED {len(decoded)}')
    if not timing_clean:
        raise SystemExit('census timing quality failed: backward TSC observed; raw snapshots were preserved')


if __name__ == '__main__':
    main()
