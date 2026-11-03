# SPDX-License-Identifier: MPL-2.0
"""Restrict only owned QMP vCPU TIDs to distinct host logical CPUs."""
import json
import os
from pathlib import Path
import select
import time
from guest_profile import query_vcpus, task_identity


class AffinityControl:
    def __init__(self, directory, pin):
        self.directory = Path(directory)
        self.fd = None
        self.meta = {'pin_vcpus': pin, 'attempted': False, 'ready': False,
                     'mutations': [], 'errors': [], 'targets': []}

    def save(self):
        (self.directory / 'affinity.json').write_text(json.dumps(self.meta, indent=2) + '\n')

    def owned(self, row=None):
        owner = self.meta['qemu']
        if select.select([self.fd], [], [], 0)[0]:
            raise ProcessLookupError('Owned QEMU exited')
        if task_identity(owner['pid'], owner['pid'])['start_ticks'] != owner['start_ticks']:
            raise RuntimeError('Owned QEMU identity changed')
        if row and task_identity(owner['pid'], row['tid'])['start_ticks'] != row['start_ticks']:
            raise RuntimeError('Owned vCPU thread identity changed')

    def start(self, owner_path):
        self.meta['attempted'] = True
        try:
            allowed = sorted(os.sched_getaffinity(0))
            if len(allowed) != 4:
                raise RuntimeError('Affinity control requires exactly four allowed host logical CPUs')
            self.meta['host_allowed'] = allowed
            self.meta['topology'] = {str(cpu): {name: (Path(f'/sys/devices/system/cpu/cpu{cpu}/topology') / name).read_text().strip()
                for name in ('core_id', 'physical_package_id', 'thread_siblings_list')} for cpu in allowed}
            pid, ticks, sample = Path(owner_path).read_text().split()
            self.meta['qemu'] = {'pid': int(pid), 'start_ticks': ticks, 'sample': sample}
            self.fd = os.pidfd_open(int(pid))
            deadline = time.monotonic() + 30
            qmp = Path(owner_path).with_suffix('.qmp')
            while True:
                self.owned()
                if time.monotonic() >= deadline:
                    raise TimeoutError('Owned vCPU affinity readiness exceeded 30 seconds')
                try:
                    mapping = query_vcpus(qmp, deadline=deadline) if qmp.exists() else []
                    if len(mapping) == 4:
                        break
                except (ConnectionRefusedError, FileNotFoundError, TimeoutError):
                    pass
                time.sleep(.05)
            tids = [cpu['thread-id'] for cpu in mapping]
            if (len(set(tids)) != 4 or any(type(tid) is not int or tid <= 0 or tid == int(pid) for tid in tids)
                    or {cpu['cpu-index'] for cpu in mapping} != set(range(4))):
                raise RuntimeError('QMP did not identify four distinct owned vCPU threads')
            self.meta['qmp_cpu_map'] = mapping
            for cpu in sorted(mapping, key=lambda entry: entry['cpu-index']):
                row = {**task_identity(int(pid), cpu['thread-id']), 'qemu_cpu_index': cpu['cpu-index'],
                       'old_allowed': sorted(os.sched_getaffinity(cpu['thread-id'])),
                       'assigned_host_cpu': allowed[cpu['cpu-index']] if self.meta['pin_vcpus'] else None}
                self.meta['targets'].append(row)
                if row['old_allowed'] != allowed:
                    raise RuntimeError('An owned vCPU does not have the four-CPU baseline mask')
            self.save()
            for row in self.meta['targets']:
                self.owned(row)
                if self.meta['pin_vcpus']:
                    os.sched_setaffinity(row['tid'], {row['assigned_host_cpu']})
                    self.meta['mutations'].append({'tid': row['tid'], 'host_logical_cpu': row['assigned_host_cpu'],
                                                   'completed_ns': time.monotonic_ns()})
                self.owned(row)
                row['after_allowed'] = sorted(os.sched_getaffinity(row['tid']))
                expected = [row['assigned_host_cpu']] if self.meta['pin_vcpus'] else allowed
                if row['after_allowed'] != expected:
                    raise RuntimeError('Owned vCPU affinity readback differs from requested mask')
                row['ready_runtime_ns'] = int(Path(f'/proc/{pid}/task/{row["tid"]}/schedstat').read_text().split()[0])
            self.meta.update(ready=True, ready_ns=time.monotonic_ns())
            self.save()
            temporary = self.directory / 'affinity-ready.tmp'
            temporary.write_text('ready\n')
            temporary.replace(self.directory / 'affinity-ready')
        except Exception as error:
            self.meta['errors'].append(f'{type(error).__name__}: {error}')
            self.save()
            (self.directory / 'affinity-failed').write_text('failed\n')

    def finish(self):
        try:
            self.meta['host_allowed_at_finish'] = sorted(os.sched_getaffinity(0))
            if 'host_allowed' in self.meta and self.meta['host_allowed_at_finish'] != self.meta['host_allowed']:
                raise RuntimeError('Controller allowed host CPUs changed during the sample')
            if self.meta['ready']:
                for row in self.meta['targets']:
                    self.owned(row)
                    row['final_allowed'] = sorted(os.sched_getaffinity(row['tid']))
                    if row['final_allowed'] != row['after_allowed']:
                        raise RuntimeError('Owned vCPU affinity changed during the workload')
        except Exception as error:
            self.meta['errors'].append(f'finish: {type(error).__name__}: {error}')
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            self.meta['finished_ns'] = time.monotonic_ns()
            self.save()
        return self.meta


def validate_affinity(directory, item):
    metadata = json.loads((directory / 'affinity.json').read_text())
    pid, ticks, sample = (directory / 'qemu.owner').read_text().split()
    assert metadata['qemu'] == {'pid': int(pid), 'start_ticks': ticks, 'sample': sample} and int(sample) == item['id']
    assert metadata['ready'] and not metadata['errors'] and metadata['pin_vcpus'] == item['pin_vcpus']
    allowed = metadata['host_allowed']
    assert len(set(allowed)) == len(allowed) == 4
    targets = metadata['targets']
    assert len(targets) == 4 and len({row['tid'] for row in targets}) == 4
    assert {row['qemu_cpu_index'] for row in targets} == set(range(4))
    assert len(metadata['mutations']) == (4 if item['pin_vcpus'] else 0)
    timeline = [json.loads(line) for line in (directory / 'redis-churn-host-timeline.jsonl').read_text().splitlines()]
    assert timeline[0]['qemu'] == metadata['qemu']['pid'] and timeline[0]['mono_before_ns'] >= metadata['ready_ns']
    assert {(cpu['cpu-index'], cpu['thread-id']) for cpu in timeline[0]['cpu_map']} == {(row['qemu_cpu_index'], row['tid']) for row in targets}
    confirmations = []
    for target in targets:
        expected = [target['assigned_host_cpu']] if item['pin_vcpus'] else allowed
        assert target['old_allowed'] == allowed and target['after_allowed'] == target['final_allowed'] == expected
        observed = [task for row in timeline if row['type'] == 'sample' for task in row['qemu_tasks'] if task['pid'] == target['tid']]
        assert observed and all(str(task['start_ticks']) == target['start_ticks'] for task in observed)
        if item['pin_vcpus']:
            confirmed = False
            for task in observed:
                if task['schedstat'][0] > target['ready_runtime_ns'] and task['cpu'] == target['assigned_host_cpu']:
                    confirmed = True
                if confirmed:
                    assert task['cpu'] == target['assigned_host_cpu']
            assert confirmed, 'Pinned target has no post-readiness CPU-assignment confirmation'
            confirmations.append(target['tid'])
    if item['pin_vcpus']:
        assert {row['assigned_host_cpu'] for row in targets} == set(allowed)
    return {'pin_vcpus': item['pin_vcpus'], 'confirmed_tids': confirmations,
            'scope': 'Host logical CPU placement only; other tasks may share them. QEMU indices are not OSTD logical CPU IDs.'}
