# SPDX-License-Identifier: MPL-2.0
"""Validate and decode a frozen batch recorder dump without asserting causality."""
import argparse
import base64
import json
from pathlib import Path
import re
import struct

MAGIC=0x4153544241544348
HWORDS,EWORDS,CAPACITY=32,20,128
STAT_NAMES=['calls','queue_max','total_cycles','max_total_cycles','max_wait_cycles','max_collect_cycles',
            'max_callback_cycles','cancelled','selected_expired','dead_at_call','invoked',
            'max_invoked_collection_lateness_ns','bad_tsc_batches','migrated_batches']
EVENT_NAMES=['commit','epoch','manager','entry_cpu','start_tsc','before_queue_tsc','acquired_tsc',
             'collected_tsc','callback_start_tsc','end_tsc','queue_before','queue_after','cancelled',
             'selected_expired','dead_at_call','invoked','invoked_collection_lateness_ns','clock_kind','exit_cpu','flags']


def decode(data):
    assert len(data)==(HWORDS+EWORDS*CAPACITY)*8
    words=struct.unpack('<'+'Q'*(len(data)//8),data)
    h=words[:HWORDS]
    assert h[:5]==(MAGIC,1,HWORDS,EWORDS,CAPACITY)
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
        assert bool(e['flags']&1)==(not valid)
        if valid:
            e.update(total_ns=(e['end_tsc']-e['start_tsc'])*1_000_000_000/freq,
                     wait_ns=(e['acquired_tsc']-e['before_queue_tsc'])*1_000_000_000/freq,
                     collect_ns=(e['collected_tsc']-e['acquired_tsc'])*1_000_000_000/freq,
                     unlock_gap_ns=(e['callback_start_tsc']-e['collected_tsc'])*1_000_000_000/freq,
                     callback_ns=(e['end_tsc']-e['callback_start_tsc'])*1_000_000_000/freq)
        events.append(e)
    assert all(x==0 for x in words[HWORDS+h[13]*EWORDS:])
    durations={k.replace('_cycles','_ns'):v*1_000_000_000/freq for k,v in stats.items() if k.endswith('_cycles')}
    return {'version':1,'frequency':freq,'epoch':h[6],'case_id':h[7],'owner_pid':h[8],
            'start_tsc':h[9],'freeze_tsc':h[10],'quiescent_tsc':h[11],
            'event_attempts':h[12],'stored_events':h[13],'dropped_events':h[14],
            'active_at_readiness_check':h[15],'threshold_ns':h[16],
            'stats':stats,'elapsed_ns':durations,'events':events,
            'complete_observation':h[14]==0 and stats['bad_tsc_batches']==0,
            'scope':'Elapsed TSC stages include interrupts, host descheduling and migration. Sums can overlap across CPUs or nested batches; they are not CPU utilization.'}


def extract(directory):
    log=(directory/'qemu.log').read_text(errors='replace')
    found={}
    for match in re.finditer(r'(?m)^TAIL_BATCH_DATA (\d+) ([A-Za-z0-9+/=]+)\r?$',log):
        case=int(match[1]);assert case not in found
        data=base64.b64decode(match[2],validate=True)
        result=decode(data);assert result['case_id']==case
        found[case]=(data,result)
    return found


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('binary',type=Path)
    args=parser.parse_args()
    print(json.dumps(decode(args.binary.read_bytes()),indent=2))
