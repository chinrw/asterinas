# SPDX-License-Identifier: MPL-2.0
"""Qualify fixed-rate windows and exact-ELF mappings after owned perf collection."""
import hashlib
import json
from pathlib import Path
import struct
from perf_data import parse
from validate_profile import validate


def elf_ranges(path):
    data=Path(path).read_bytes()
    assert data[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<H',data,18)[0]==62
    offset=struct.unpack_from('<Q',data,32)[0]
    size,count=struct.unpack_from('<HH',data,54)
    assert size==56 and offset+size*count<=len(data)
    result=[]
    for i in range(count):
        kind,flags,off,addr,physical,filesz,memsz,align=struct.unpack_from('<II6Q',data,offset+i*size)
        if kind==1 and flags&1:
            assert memsz>0 and off+filesz<=len(data)
            result.append([hex(addr),hex(addr+memsz)])
    assert result
    return result,hashlib.sha256(data).hexdigest()


def validate_guest_profile(directory,kernel):
    windows=[]
    for mode in ['none','churn','expiry']:
        for service in ['nginx','redis']:
            key=f'{service}-{mode}'
            data=json.loads((directory/f'{key}-fixed.json').read_text())
            assert data['planned']==data['completed']==len(data['records'])
            windows.append({'name':key,'clock':'CLOCK_MONOTONIC',
                            'start_ns':min(x['scheduled_ns'] for x in data['records']),
                            'end_ns':max(x['finished_ns'] for x in data['records'])})
    ranges,digest=elf_ranges(kernel)
    (directory/'kernel-exec-ranges.json').write_text(json.dumps(ranges)+'\n')
    (directory/'guest-perf-windows.json').write_text(json.dumps(windows,indent=2)+'\n')
    data=parse(directory/'guest-perf.data')
    (directory/'guest-perf-samples.json').write_text(json.dumps(data)+'\n')
    report=validate(data,json.loads((directory/'guest-perf.json').read_text()),windows,ranges)
    report.update(kernel_elf_sha256=digest,window_scope='Six fixed-rate request windows. The process recorder spans the preceding workload history, but individual micro/warmup/Go windows are not reconstructed from elapsed-only phase records.')
    (directory/'guest-perf-validation.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
