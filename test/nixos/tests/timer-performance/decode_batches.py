# SPDX-License-Identifier: MPL-2.0
"""Validate and decode a frozen batch recorder dump without asserting causality."""
import argparse
import base64
import json
from pathlib import Path
import re
import struct

MAGIC=0x4153544241544348
HWORDS,EWORDS,CAPACITY=32,34,128
TIMER_WORDS=HWORDS+EWORDS*CAPACITY
HEAP_MAGIC=0x4153544845415044
HEAP_HWORDS,HEAP_EWORDS,HEAP_CAPACITY=16,16,128
BLOB_WORDS=TIMER_WORDS+HEAP_HWORDS+HEAP_EWORDS*HEAP_CAPACITY
STAT_NAMES=['calls','queue_max','total_cycles','max_total_cycles','max_wait_cycles','max_collect_cycles',
            'max_callback_cycles','cancelled','selected_expired','dead_at_call','invoked',
            'max_invoked_collection_lateness_ns','bad_tsc_batches','migrated_batches']
EVENT_NAMES=['commit','epoch','manager','entry_cpu','start_tsc','before_queue_tsc','acquired_tsc',
             'collected_tsc','callback_start_tsc','end_tsc','queue_before','queue_after','cancelled',
             'selected_expired','dead_at_call','invoked','invoked_collection_lateness_ns','clock_kind','exit_cpu','flags']


DETAIL_STAGES=['clock_read','cancelled_pop_and_drop','selected_pop','vector_push','callback_timer_lock','callback_periodic_rearm','registered_callback']
EVENT_NAMES += [name+'_'+kind+'_cycles' for name in DETAIL_STAGES for kind in ['sum','max']]

HEAP_STAT_NAMES=['bad_or_incomplete_calls','call_count','total_wait_cycles','max_wait_cycles','total_hold_cycles','max_hold_cycles','alloc_attempts','free_attempts']
HEAP_EVENT_NAMES=['commit','epoch','entry_cpu','kind','size_class','start_tsc','acquired_tsc','before_release_tsc','released_tsc','cache_before_bytes','cache_after_bytes','attempted_ops','flags','exit_cpu','reserved0','reserved1']


def decode_heap(words,timer_header,freq):
    h=words[:HEAP_HWORDS]
    assert h[:5]==(HEAP_MAGIC,3,HEAP_HWORDS,HEAP_EWORDS,HEAP_CAPACITY)
    assert h[6]==min(h[5],HEAP_CAPACITY) and h[7]==h[5]-h[6]
    stats=dict(zip(HEAP_STAT_NAMES,h[8:16]))
    assert stats['call_count']>=h[5] and stats['bad_or_incomplete_calls']<=stats['call_count']
    events=[]
    for i in range(h[6]):
        v=words[HEAP_HWORDS+i*HEAP_EWORDS:HEAP_HWORDS+(i+1)*HEAP_EWORDS]
        e=dict(zip(HEAP_EVENT_NAMES,v))
        assert e['commit']==i+1 and e['epoch']==timer_header[6]
        assert e['kind'] in (0,1) and e['size_class'] in (8,16,32,64,128,256,512,1024,2048)
        assert not e['reserved0'] and not e['reserved1'] and not e['flags']&~7
        assert bool(e['flags']&4)==(e['entry_cpu']!=e['exit_cpu'])
        times=v[5:9];valid=all(times) and all(a<=b for a,b in zip(times,times[1:]))
        assert valid or e['flags']&1
        if not e['flags']:
            assert timer_header[9]<=e['start_tsc']<=e['released_tsc']<=timer_header[11]
            assert e['attempted_ops']>0
            e.update(wait_ns=(e['acquired_tsc']-e['start_tsc'])*1_000_000_000/freq,
                     hold_ns=(e['before_release_tsc']-e['acquired_tsc'])*1_000_000_000/freq,
                     release_gap_ns=(e['released_tsc']-e['before_release_tsc'])*1_000_000_000/freq,
                     total_ns=(e['released_tsc']-e['start_tsc'])*1_000_000_000/freq)
        events.append(e)
    assert stats['bad_or_incomplete_calls']>=sum(bool(e['flags']) for e in events)
    assert all(x==0 for x in words[HEAP_HWORDS+h[6]*HEAP_EWORDS:])
    return {'stats':stats,'event_attempts':h[5],'stored_events':h[6],'dropped_events':h[7],
            'elapsed_ns':{k.replace('_cycles','_ns'):v*1_000_000_000/freq for k,v in stats.items() if k.endswith('_cycles')},
            'events':events,'complete_observation':h[7]==0 and stats['bad_or_incomplete_calls']==0,
            'scope':'Only shared slab-pool refill/drain scopes admitted during this epoch. Local cache fast paths and large allocations are excluded. Wait/hold are elapsed intervals, not CPU time.'}


def decode(data):
    assert len(data)==BLOB_WORDS*8
    words=struct.unpack('<'+'Q'*(len(data)//8),data)
    h=words[:HWORDS]
    assert h[:5]==(MAGIC,3,HWORDS,EWORDS,CAPACITY)
    freq=h[5]
    assert freq>0 and h[6]>0 and h[7]>0 and h[8]>0
    assert h[9]<=h[10]<=h[11]
    assert h[13]==min(h[12],CAPACITY) and h[14]==h[12]-h[13]
    assert h[15]==0 and h[17]==0
    stats=dict(zip(STAT_NAMES,h[18:32]))
    assert stats['dead_at_call']+stats['invoked']<=stats['selected_expired']
    stats['cancelled_after_selection']=stats['selected_expired']-stats['dead_at_call']-stats['invoked']
    assert stats['calls']>=h[12]
    events=[]
    for i in range(h[13]):
        v=words[HWORDS+i*EWORDS:HWORDS+(i+1)*EWORDS]
        e=dict(zip(EVENT_NAMES,v))
        assert e['commit']==i+1 and e['epoch']==h[6]
        assert e['queue_before']-e['queue_after']==e['cancelled']+e['selected_expired']
        assert e['dead_at_call']+e['invoked']<=e['selected_expired']
        e['cancelled_after_selection']=e['selected_expired']-e['dead_at_call']-e['invoked']
        assert e['clock_kind'] in (0,1) and e['flags']&~7==0
        assert bool(e['flags']&4)==(e['entry_cpu']!=e['exit_cpu'])
        assert e['start_tsc']>=h[9] and e['end_tsc']<=h[11]
        times=v[4:10]
        valid=all(a<=b for a,b in zip(times,times[1:]))
        assert valid or e['flags']&1
        for name in DETAIL_STAGES:
            assert e[name+'_max_cycles']<=e[name+'_sum_cycles']
        if valid and not e['flags']&1:
            e.update(setup_ns=(e['before_queue_tsc']-e['start_tsc'])*1_000_000_000/freq,
                     total_ns=(e['end_tsc']-e['start_tsc'])*1_000_000_000/freq,
                     wait_ns=(e['acquired_tsc']-e['before_queue_tsc'])*1_000_000_000/freq,
                     collect_ns=(e['collected_tsc']-e['acquired_tsc'])*1_000_000_000/freq,
                     unlock_gap_ns=(e['callback_start_tsc']-e['collected_tsc'])*1_000_000_000/freq,
                     callback_ns=(e['end_tsc']-e['callback_start_tsc'])*1_000_000_000/freq)
            e['detail_ns']={name:{kind:e[name+'_'+kind+'_cycles']*1_000_000_000/freq for kind in ['sum','max']} for name in DETAIL_STAGES}
        events.append(e)
    assert stats['bad_tsc_batches']>=sum(bool(e['flags']&1) for e in events)
    assert stats['migrated_batches']>=sum(bool(e['flags']&4) for e in events)
    assert all(x==0 for x in words[HWORDS+h[13]*EWORDS:TIMER_WORDS])
    durations={k.replace('_cycles','_ns'):v*1_000_000_000/freq for k,v in stats.items() if k.endswith('_cycles')}
    result={'version':3,'frequency':freq,'epoch':h[6],'case_id':h[7],'owner_pid':h[8],
            'start_tsc':h[9],'freeze_tsc':h[10],'quiescent_tsc':h[11],
            'event_attempts':h[12],'stored_events':h[13],'dropped_events':h[14],
            'active_at_readiness_check':h[15],'threshold_ns':h[16],
            'stats':stats,'elapsed_ns':durations,'events':events,
            'complete_observation':h[14]==0 and stats['bad_tsc_batches']==0,
            'scope':'Elapsed TSC stages include interrupts, host descheduling and migration. Sums can overlap across CPUs or nested batches; they are not CPU utilization.'}
    result['heap']=decode_heap(words[TIMER_WORDS:],h,freq)
    result['complete_observation'] &= result['heap']['complete_observation']
    return result


def extract(directory):
    log=(directory/'qemu.log').read_text(errors='replace')
    found={}
    for match in re.finditer(r'(?m)^TAIL_BATCH_DATA (\d+) ([A-Za-z0-9+/=]+)\r?$',log):
        case=int(match[1]);assert case not in found
        data=base64.b64decode(match[2],validate=True)
        result=decode(data);assert result['case_id']==case
        found[case]=(data,result)
    chunks={}
    for match in re.finditer(r'(?m)^TAIL_BATCH_CHUNK (\d+) (\d+) (\d+) ([A-Za-z0-9+/=]+)\r?$',log):
        case,index,total=map(int,match.group(1,2,3))
        assert total==68 and 1<=index<=total
        parts=chunks.setdefault(case,{})
        assert index not in parts
        parts[index]=match[4]
    for case,parts in chunks.items():
        assert case not in found and set(parts)==set(range(1,69))
        data=base64.b64decode(''.join(parts[i] for i in range(1,69)),validate=True)
        result=decode(data);assert result['case_id']==case
        found[case]=(data,result)
    return found


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('binary',type=Path)
    args=parser.parse_args()
    print(json.dumps(decode(args.binary.read_bytes()),indent=2))
