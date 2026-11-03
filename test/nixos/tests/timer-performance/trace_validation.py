# SPDX-License-Identifier: MPL-2.0
"""Require complete observation windows as well as complete request records."""
import json
import os
from tail_observe import tcpdump_drops
from packet_coverage import stream_coverage

def validate_traces(directory, cases=None):
    if cases is None:
        cases=[(service,mode) for mode in ['none','churn','expiry'] for service in ['nginx','redis']]
    for service,mode in cases:
        key=f'{service}-{mode}'
        data=json.loads((directory/f'{key}-fixed.json').read_text())
        timeline=[json.loads(x) for x in (directory/f'{key}-host-timeline.jsonl').read_text().splitlines()]
        assert timeline[0]['type']=='clock_start' and timeline[-1]['type']=='clock_end'
        assert timeline[-1]['stop_requested'] and timeline[-1]['samples']>0
        if os.environ.get('TAIL_QMP') in {'1', 'map'}:
            assert timeline[0]['qmp_enabled']
            if os.environ.get('TAIL_QMP')=='map':
                assert timeline[0]['qmp_mode']=='map'
                assert not any(e['type']=='guest_snapshot' for e in timeline)
            args=json.loads((directory/'qemu.argv.json').read_text())
            cpus=int(args[args.index('-smp')+1])
            assert {cpu['cpu-index'] for cpu in timeline[0]['cpu_map']}==set(range(cpus))
            for event in timeline:
                if event['type']=='guest_snapshot':
                    snap=event['snapshot']
                    assert snap['intrusive'] and snap['resumed_ns']>=snap['stop_requested_ns']
        assert timeline[0]['mono_before_ns']<=min(r['scheduled_ns'] for r in data['records'])
        assert timeline[-1]['mono_after_ns']>=max(r['finished_ns'] for r in data['records'])
        samples=[r for r in timeline if r['type']=='sample']
        assert len(samples)==timeline[-1]['samples']
        assert all(r['sample_finished_ns']>=r['mono_ns'] and r['qemu_tasks'] for r in samples)
        assert all(a['mono_ns']<=b['mono_ns'] for a,b in zip(samples,samples[1:]))
        capture=json.loads((directory/f'{key}-fixed-capture.json').read_text())
        assert capture['exit_code']==0
        assert capture['wire_coverage']['complete']
        port=capture['server_port']
        assert stream_coverage(directory/f'{key}-fixed.pcap',data['records'],port)['complete']
        assert capture['clock_start']['mono_before_ns']<=min(r['scheduled_ns'] for r in data['records'])
        assert capture['clock_end']['mono_after_ns']>=max(r['finished_ns'] for r in data['records'])
        assert (directory/f'{key}-fixed.pcap').stat().st_size>24
        assert tcpdump_drops((directory/f'{key}-fixed-tcpdump.log').read_text())==0
        assert all('connection_id' in r and 'local_port' in r for r in data['records'])
