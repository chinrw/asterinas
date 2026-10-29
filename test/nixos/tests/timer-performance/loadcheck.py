# SPDX-License-Identifier: MPL-2.0
"""Validate the owned producer and guest-clock coverage of one workload."""
import json
from pathlib import Path
import sys

key=sys.argv[1]
root=Path('/var/tmp')
log=(root/f'timer-load-{key}.log').read_text().splitlines()
def row(lines,prefix):
    matches=[json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
    assert len(matches)==1,(prefix,matches)
    return matches[0]
start=row(log,'PERF_LOAD_STARTED ')
finish=row(log,'PERF_LOAD_FINISHED ')
pid=int((root/f'timer-load-{key}.pid').read_text())
begin=row((root/f'{key}.begin').read_text().splitlines(),'PERF_STAMP ')
end=row((root/f'{key}.end').read_text().splitlines(),'PERF_STAMP ')
assert begin['label']==key+'_begin' and end['label']==key+'_end'
assert start['pid']==finish['pid']==pid
assert start['kind']==finish['kind']==key.split('_')[0]
assert finish['stop_requested'] is True
assert start['start_ns']==finish['start_ns']
assert start['start_ns']<=begin['now_ns']<end['now_ns']<=finish['finish_ns']
assert finish['elapsed_ns']==finish['finish_ns']-finish['start_ns']>0
achieved=finish['cycles']*1e9/finish['elapsed_ns']
if start['kind']=='churn':
    assert achieved >= start['rate']*.90,('producer below frozen 90% rate gate',achieved,start['rate'])
else:
    assert finish['expirations']>0,finish
print('PERF_LOAD_VALIDATED '+json.dumps({'key':key,'pid':pid,'kind':start['kind'],
    'offered_cycles_per_second':start['rate'] if start['kind']=='churn' else None,
    'achieved_cycles_per_second':achieved,'expirations':finish['expirations'],
    'skipped_slots':finish['skipped_slots'],'producer_elapsed_ns':finish['elapsed_ns'],
    'workload_start_ns':begin['now_ns'],'workload_end_ns':end['now_ns'],
    'producer_start_ns':start['start_ns'],'producer_finish_ns':finish['finish_ns']}))
