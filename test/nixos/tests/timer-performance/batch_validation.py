# SPDX-License-Identifier: MPL-2.0
"""Require exact epoch, count and clock identities for each temporary probe window."""
import json
from decode_batches import extract


def validate_batches(directory, enabled):
    dumps=extract(directory)
    controls=list(directory.glob('*-batch-control.json'))
    if not enabled:
        assert not dumps and not controls
        return
    keys={f'{service}-{mode}' for mode in ['none','churn','expiry'] for service in ['nginx','redis']}
    assert {p.name.removesuffix('-batch-control.json') for p in controls}==keys
    assert len(dumps)==len(controls)==6
    epochs=[]
    for path in controls:
        c=json.loads(path.read_text());b=dumps[c['case_id']][1]
        assert c['complete'] and c['case']==path.name.removesuffix('-batch-control.json')
        assert b['epoch']==c['start']['epoch']==c['freeze']['epoch']
        assert b['frequency']==c['start']['frequency']==c['freeze']['frequency']
        assert b['complete_observation'] and b['stats']['calls']>0 and b['threshold_ns']==5_000_000
        assert c['freeze']['dump_bytes']==20736
        fixed=json.loads(path.with_name(c['case']+'-fixed.json').read_text())
        assert c['start']['host_after_ns']<=min(r['scheduled_ns'] for r in fixed['records'])
        assert c['freeze']['host_before_ns']>=max(r['finished_ns'] for r in fixed['records'])
        epochs.append(b['epoch'])
        for name in ['clock_before','clock_after']:
            cal=c[name]
            assert cal['frequency']==b['frequency'] and cal['offset_lower_ns']<=cal['offset_upper_ns']
            assert len(cal['samples'])==5
    assert sorted(epochs)==list(range(1,7))
