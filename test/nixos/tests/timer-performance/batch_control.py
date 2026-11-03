# SPDX-License-Identifier: MPL-2.0
"""Control the guest's temporary batch recorder and bracket its TSC clock."""
import contextlib
import errno
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import time
import zlib

MAGIC=0x4153544241544348
REQUEST=struct.Struct('<5Q')
REPLY=struct.Struct('<12Q')
PING,CLOCK,START,FREEZE=range(4)


def call(command,case_id=0,timeout=5):
    host=os.environ.get('PERF_CLIENT_HOST','10.0.2.15')
    nonce=secrets.randbits(64)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout);sock.connect((host,9998))
        before=time.monotonic_ns()
        sock.send(REQUEST.pack(MAGIC,1,command,nonce,case_id))
        packet=sock.recv(REPLY.size+1)
        after=time.monotonic_ns()
    assert len(packet)==REPLY.size, 'invalid batch-control reply length'
    words=REPLY.unpack(packet)
    assert words[:5]==(MAGIC,1,command,nonce,case_id), 'batch-control reply identity mismatch'
    result=dict(zip(['magic','version','command','nonce','case_id','status_errno','tsc_before','tsc_after',
                     'frequency','epoch','dump_bytes','busy_retries'],words))
    result.update(host_before_ns=before,host_after_ns=after,rtt_ns=after-before)
    if result['status_errno']:
        raise OSError(result['status_errno'],f'guest batch-control command {command}: {result}')
    assert result['frequency']>0 and result['tsc_before']<=result['tsc_after']
    return result


def calibrate():
    samples=[call(CLOCK) for _ in range(5)]
    frequencies={s['frequency'] for s in samples}
    assert len(frequencies)==1
    freq=samples[0]['frequency']
    # The guest's TSC reads occur between host send and receive. Keep every
    # interval; do not silently select a contradictory clock observation away.
    for s in samples:
        s['offset_lower_ns']=s['host_before_ns']-(s['tsc_after']*1_000_000_000+freq-1)//freq
        s['offset_upper_ns']=s['host_after_ns']-s['tsc_before']*1_000_000_000//freq
    low=max(s['offset_lower_ns'] for s in samples)
    high=min(s['offset_upper_ns'] for s in samples)
    assert low<=high, 'inconsistent clock calibration intervals'
    return {'frequency':freq,'offset_lower_ns':low,'offset_upper_ns':high,'samples':samples}


@contextlib.contextmanager
def batch_window(service,mode):
    if os.environ.get('TAIL_BATCH')!='1':
        yield
        return
    assert os.environ.get('PERF_NETDEV')=='tap', 'batch-control channel requires the declared TAP route'
    directory=Path(os.environ['PERF_SAMPLE_DIR'])
    key=f'{service}-{mode}'
    case_id=zlib.crc32(key.encode()) or 1
    result={'case':key,'case_id':case_id,'complete':False,'guest_file':f'/var/tmp/timer-batch-{case_id}.bin'}
    started=False
    try:
        result['clock_before']=calibrate()
        result['start']=call(START,case_id)
        started=True
        yield
    finally:
        try:
            if started:
                result['freeze']=call(FREEZE,case_id,timeout=10)
                result['clock_after']=calibrate()
                assert result['freeze']['epoch']==result['start']['epoch']
                assert result['freeze']['frequency']==result['clock_before']['frequency']==result['clock_after']['frequency']
                assert result['freeze']['dump_bytes']==20736
                before,after=result['clock_before'],result['clock_after']
                low=max(before['offset_lower_ns'],after['offset_lower_ns'])
                high=min(before['offset_upper_ns'],after['offset_upper_ns'])
                result['clock_relation']={
                    'constant_offset_feasible':low<=high,
                    'constant_offset_gap_ns':max(0,low-high),
                    'endpoint_envelope_lower_ns':min(before['offset_lower_ns'],after['offset_lower_ns']),
                    'endpoint_envelope_upper_ns':max(before['offset_upper_ns'],after['offset_upper_ns']),
                    'scope':'Endpoint calibration only. Interior interpolation assumes an affine host/guest clock relation; successful collection does not prove a constant offset.'}
                result['complete']=True
        finally:
            temporary=directory/f'{key}-batch-control.tmp'
            temporary.write_text(json.dumps(result,indent=2)+'\n')
            temporary.replace(directory/f'{key}-batch-control.json')


if __name__=='__main__':
    print(json.dumps(call(PING),indent=2))
