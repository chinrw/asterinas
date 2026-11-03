# SPDX-License-Identifier: MPL-2.0
"""Qualify fixed-rate windows and exact-ELF mappings after owned perf collection."""
import hashlib
import json
from pathlib import Path
import struct
from perf_data import parse
from stop_validation import corrected_validate


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


def validate_vcpu_profile(data,metadata,windows,ranges,log):
    report=corrected_validate(data,metadata,windows,ranges,log)
    expected={entry['tid'] for entry in metadata.get('target_threads',[])}
    count=metadata.get('expected_cpus')
    indices={entry.get('qemu_cpu_index') for entry in metadata.get('target_threads',[])}
    if (type(count) is not int or count not in (1,4) or data['inherit'] or len(expected)!=count
            or len(metadata.get('target_threads',[]))!=count
            or indices!=set(range(count)) or any(row['tid'] not in expected for row in data['samples'])):
        report['errors'].append('Raw inheritance, samples, target count, or QMP indices differ from expected owned vCPU threads')
        report['valid']=False
    return report


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
    metadata=json.loads((directory/'guest-perf.json').read_text())
    report=validate_vcpu_profile(data,metadata,windows,ranges,(directory/'guest-perf.log').read_text(errors='replace'))
    report.update(kernel_elf_sha256=digest,window_scope='Six fixed-rate request windows. The vCPU recorder spans the preceding workload history, but individual micro/warmup/Go windows are not reconstructed from elapsed-only phase records.')
    (directory/'guest-perf-validation.json').write_text(json.dumps(report,indent=2)+'\n')
    return report
