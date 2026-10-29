# SPDX-License-Identifier: MPL-2.0
"""Fail closed on incomplete plans, mismatched identities, or malformed results."""
import json
from pathlib import Path
import re

GO_SHA='1a6b8c133239c4b7792709d25bb4bb4b5eded497e79f1d36e2a62066df9a623d'

def validate_go(go,item,owner):
    pid,ticks,number=map(int,owner.read_text().split())
    expected={'sample_id':item['id'],'qemu_pid':pid,'qemu_start_ticks':ticks,
              'helper':'minimal','helper_sha256':GO_SHA,'deadline_ms':3000,
              'endpoint':'command_exit_and_barrier'}
    assert number==item['id']
    for key,value in expected.items():assert go.get(key)==value,(key,go)
    assert type(go.get('elapsed_ns')) is int and go['elapsed_ns']>=0
    assert go['outcome'] in {'pass','deadline'}
    if go['outcome']=='pass':
        assert go['elapsed_ns']<=3000000000 and go['error_kind']=='none' and go['exit_status'] in (None,0)
    else:assert go['elapsed_ns']>3000000000

def validate_performance(directory,item):
    phases=[json.loads(line) for line in (directory/'phases.jsonl').read_text().splitlines()]
    expected={'identity','services_start','services_stop','client_ready_none'}
    populations=[64] if item['pilot'] else [1,64,1024,4096]
    expected|={f'micro_{n}_{mode}' for n in populations for mode in ['cancel','drop','rearm','expiry']}
    for mode in ['none','churn','expiry']:
        for workload in ['probe','wake','nginx','redis','sqlite']:
            expected|={f'begin_{mode}_{workload}',f'end_{mode}_{workload}'}
            expected.add(f'client_{workload}_{mode}' if workload in ['nginx','redis'] else f'{workload}_{mode}')
            if mode!='none':expected|={f'load_start_{mode}_{workload}',f'load_stop_{mode}_{workload}'}
    assert len(phases)==len(expected) and {p['name'] for p in phases}==expected
    assert all(p['passed'] is True and type(p['host_elapsed_ns']) is int and p['host_elapsed_ns']>0
               and type(p['qemu_cpu_ticks']) is int and p['qemu_cpu_ticks']>=0 for p in phases)
    log=(directory/'qemu.log').read_text(errors='replace')
    def records(prefix):
        return [json.loads(m[1]) for m in re.finditer(r'(?m)^'+prefix+r' (\{[^\r\n]+\})\r?$',log)]
    micros=records('PERF_MICRO')
    assert len(micros)==len(populations)*4
    assert {(r['n'],r['mode']) for r in micros}=={(n,m) for n in populations for m in ['cancel','drop','rearm','expiry']}
    for row in micros:
        assert row['expirations']==(row['n'] if row['mode'] in ['expiry','rearm'] else 0)
        assert all(type(v) is int and v>=0 for k,v in row.items() if k!='mode')
    probes=records('PERF_PROBE');wakes=records('PERF_WAKE')
    assert len(probes)==len(wakes)==3
    for r in probes:
        assert type(r['chunks']) is int and r['chunks']>0 and r['operations']==r['chunks']*4096
        assert r['elapsed_ns']>0 and 0<=r['gap_p99_ns']<=r['gap_max_ns']
    for r in wakes:
        assert r['samples']==(200 if item['pilot'] else 1000) and r['period_ns']==5000000
        assert 0<=r['p50_ns']<=r['p99_ns']<=r['max_ns']
    loads=records('PERF_LOAD_VALIDATED')
    assert len(loads)==10 and {x['key'] for x in loads}=={f'{m}_{w}' for m in ['churn','expiry'] for w in ['probe','wake','nginx','redis','sqlite']}
    for x in loads:
        assert x['producer_start_ns']<=x['workload_start_ns']<x['workload_end_ns']<=x['producer_finish_ns']
        assert x['achieved_cycles_per_second']>0
        if x['kind']=='churn':assert x['achieved_cycles_per_second']>=x['offered_cycles_per_second']*.9
        else:assert x['expirations']>0
    for mode in ['none','churn','expiry']:
        for service in ['nginx','redis']:
            result=json.loads((directory/f'{service}-{mode}.json').read_text())
            assert result['service']==service and result['mode']==mode and result['exit_code']==0
            rates=[result['rps']] if service=='nginx' else [result['set_rps'],result['get_rps']]
            assert all(type(x) in (int,float) and x>0 for x in rates)
            fixed=json.loads((directory/f'{service}-{mode}-fixed.json').read_text())
            n=fixed['offered_rps']*fixed['duration_s']
            assert fixed['service']==service and fixed['planned']==fixed['completed']==len(fixed['records'])==n
            assert {x['id'] for x in fixed['records']}==set(range(n))
            for row in fixed['records']:
                assert row['error'] is None
                assert row['scheduled_ns']<=row['started_ns']<=row['finished_ns']
                assert row['latency_ns']==row['finished_ns']-row['scheduled_ns']
    return {'phases':len(phases),'micro_records':len(micros),'load_records':len(loads),
            'probe_records':3,'wake_records':3,'service_cases':6,'fixed_rate_cases':6}
