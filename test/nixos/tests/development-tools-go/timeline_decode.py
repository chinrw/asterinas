# SPDX-License-Identifier: MPL-2.0
import argparse
import json
from pathlib import Path
import re
import struct

FIELDS='tid ns_last ns_min ns_max call_seq active entry_tsc wait_requested blocked_current block_tsc wake_tsc enqueue_tsc run_tsc deadline_ns wake_ns callback_tsc ready_tsc ready_kind calls ended calls_blocked calls_no_block switchouts preempts timer_wakes other_wakes runs total_cycles max_cycles max_late_ns max_wake_run_cycles reserved31 eintr bad_time select_tsc wait_attempts total_wake_run_cycles queued_wakes block_events max_select_run_cycles'.split()
KINDS={1:'enter',2:'wait',3:'block',4:'timer_wake',5:'other_wake',6:'enqueue',7:'selected',8:'run',9:'end',10:'exit',11:'preempt',12:'softirq_begin',13:'softirq_end',14:'timer_collect',15:'timer_batch_end',16:'slow_timer_insert',17:'daemon_wake_enqueue',18:'daemon_selected',19:'daemon_run',20:'daemon_switchout',21:'daemon_fair_wake',22:'fair_pick_sample',23:'daemon_fair_requeue'}
def decode(file):
    data=file.read_bytes();header=struct.unpack_from('<16Q',data)
    magic,version,nslots,capacity,ewords,swords,hz,pid,start,stop,total,stored,dropped,collisions,mode,_=header
    assert magic==0x41535454494d4531 and version in (1,2,3) and swords==len(FIELDS) and ewords==12
    header_bytes={1:128,2:192,3:384}[version]
    softirq=None
    if version>=2:
        count,cycles,maximum,*_=struct.unpack_from('<8Q',data,128)
        softirq={'calls':count,'total_ms':cycles*1000/hz,'max_ms':maximum*1000/hz}
    timer_stats=None
    if version==3:
        names='max_softirq_gap_cycles inserts max_heap_len max_insert_cycles collections max_collect_cycles total_collect_cycles max_batch_cycles total_batch_cycles drained cancelled max_batch_len dead_callbacks live_callbacks'.split()
        timer_stats=dict(zip(names,struct.unpack_from('<24Q',data,192)))
        for name,value in list(timer_stats.items()):
            if name.endswith('_cycles'):timer_stats[name.removesuffix('_cycles')+'_ms']=value*1000/hz
    assert len(data)==header_bytes+nslots*swords*8+stored*ewords*8
    offset=header_bytes;slots=[]
    for _ in range(nslots):
        row=dict(zip(FIELDS,struct.unpack_from('<'+str(swords)+'Q',data,offset)));offset+=swords*8
        if not row['tid']:continue
        row['completed_blocked']=row['ended']-row['calls_no_block']
        row['no_block_fraction']=row['calls_no_block']/row['ended'] if row['ended'] else None
        row['max_call_ms']=row['max_cycles']*1000/hz
        row['max_wake_run_ms']=row['max_wake_run_cycles']*1000/hz
        row['max_late_ms']=row['max_late_ns']/1e6
        slots.append(row)
    events=[]
    for index in range(stored):
        seq,tsc,kind,tid,call,ns,a,b,c,active,_,_=struct.unpack_from('<12Q',data,offset);offset+=ewords*8
        assert seq==index+1
        events.append({'seq':seq,'tsc':tsc,'seconds':(tsc-start)/hz,'kind':KINDS[kind],'tid':tid,'call':call,'ns':ns,'a':a,'b':b,'c':c,'active':active})
    log=file.with_suffix('.child.log');text=log.read_text(errors='replace') if log.exists() else ''
    tags=[int(x) for x in re.findall(r'(?m)^TIMELINE_SYSMON_TID (\d+)\s*$',text)]
    sysmon=tags[0] if len(tags)==1 else None
    calls={}
    for event in events:
        if event['tid']!=sysmon or not event['active']:continue
        call=calls.setdefault(event['call'],{'call':event['call'],'ns':event['ns']})
        call.setdefault(event['kind'],event)
    timeline=[]
    for call in calls.values():
        row={'call':call['call'],'ns':call['ns']}
        for kind in KINDS.values():
            if kind in call:row[kind+'_seconds']=call[kind]['seconds']
        if 'timer_wake' in call:
            wake=call['timer_wake'];row['timer_late_ms']=(wake['b']-wake['a'])/1e6
        if 'enqueue' in call and 'run' in call:
            row['wake_run_ms']=(call['run']['tsc']-call['enqueue']['tsc'])*1000/hz
        timeline.append(row)
    return {'file':str(file),'version':version,'pid':pid,'mode':mode,'tsc_hz':hz,'window_seconds':(stop-start)/hz,'events_total':total,'events_stored':stored,'events_dropped':dropped,'collisions':collisions,'timer_softirq':softirq,'timer_queue':timer_stats,'softirq_task':struct.unpack_from('<Q',data,38*8)[0] if version==3 else None,'sysmon_tid':sysmon,'sysmon_stats':next((s for s in slots if s['tid']==sysmon),None),'threads':slots,'sysmon_calls':timeline,'events':events}
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('files',nargs='+',type=Path);args=parser.parse_args()
    for file in args.files:
        out=decode(file);file.with_suffix('.json').write_text(json.dumps(out,indent=2)+'\n')
        foreign=[s for s in out['threads'] if s['ns_min']==s['ns_max']==1000]
        ended=sum(s['ended'] for s in foreign);no_block=sum(s['calls_no_block'] for s in foreign)
        print(file.name,'mode',out['mode'],'sysmon',out['sysmon_tid'],'1us ended',ended,'no_block',no_block,'sysmon_stats',out['sysmon_stats'])
