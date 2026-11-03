# SPDX-License-Identifier: MPL-2.0
"""Collect host scheduling and packet timestamps for owned benchmark cases."""
import contextlib
import gc
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from packet_coverage import stream_coverage


def tcpdump_drops(text):
    counts=re.findall(r'(?m)^(\d+) packets dropped by kernel\s*$',text)
    if len(counts)!=1:
        raise ValueError('missing or ambiguous tcpdump kernel-drop count')
    return int(counts[0])


def clock_pair():
    before = time.monotonic_ns()
    wall = time.time_ns()
    return {'mono_before_ns':before, 'realtime_ns':wall, 'mono_after_ns':time.monotonic_ns()}


def proc_info(pid, task_owner=None):
    root = Path('/proc')/str(pid) if task_owner is None else Path('/proc')/str(task_owner)/'task'/str(pid)
    stat = (root/'stat').read_text().rsplit(') ',1)[1].split()
    return {'pid':pid, 'comm':(root/'comm').read_text().strip(), 'state':stat[0],
            'start_ticks':int(stat[19]), 'cpu':int(stat[36]),
            'schedstat':[int(x) for x in (root/'schedstat').read_text().split()]}


def watch(output, stop, ready, owner, client):
    qemu, start, _ = map(int, Path(owner).read_text().split())
    assert proc_info(qemu)['start_ticks']==start
    client_start=proc_info(client)['start_ticks']
    schedstats=Path('/proc/sys/kernel/sched_schedstats').read_text().strip()
    if os.environ.get('TAIL_REQUIRE_SCHEDSTATS')=='1':
        assert schedstats=='1', 'scheduler wait accounting is disabled'
    deadline = time.monotonic()+300
    count = 0
    qmp=None;cpus=[];snapshot_done=False
    trigger=Path(output.replace('-host-timeline.jsonl','-slow-trigger.json'))
    if os.environ.get('TAIL_QMP') in {'1', 'map'}:
        from qmp_snapshot import Qmp
        qmp=Qmp(Path(owner).with_suffix('.qmp'))
        cpus=qmp.call('query-cpus-fast')
        assert all((Path('/proc')/str(qemu)/'task'/str(c['thread-id'])).exists() for c in cpus)
    with Path(output).open('w') as stream:
        stream.write(json.dumps({'type':'clock_start', **clock_pair(), 'qemu':qemu, 'client':client,
                                 'sched_schedstats':schedstats,'cpu_map':cpus,'qmp_enabled':qmp is not None,'qmp_mode':os.environ.get('TAIL_QMP','0')})+'\n')
        Path(ready).write_text('ready\n')
        while not Path(stop).exists() and time.monotonic()<deadline:
            before = time.monotonic_ns()
            tasks = []
            try:
                assert proc_info(qemu)['start_ticks']==start
                for tid in (Path('/proc')/str(qemu)/'task').iterdir():
                    try:
                        tasks.append(proc_info(int(tid.name),task_owner=qemu))
                    except (FileNotFoundError, ProcessLookupError):
                        pass
                client_info = proc_info(client)
                assert client_info['start_ticks']==client_start
            except (FileNotFoundError, ProcessLookupError):
                break
            row = {'type':'sample', 'mono_ns':before, 'qemu_tasks':tasks, 'client':client_info}
            if count % 10 == 0:
                for name, path in [('cpu_pressure','/proc/pressure/cpu'), ('cgroup_cpu','/sys/fs/cgroup/cpu.stat'),
                                   ('cgroup_cpu_pressure','/sys/fs/cgroup/cpu.pressure'), ('memory_events','/sys/fs/cgroup/memory.events')]:
                    try:
                        row[name]=Path(path).read_text()
                    except OSError as error:
                        row[name]={'unavailable':str(error)}
                row['host_cpu_stat']=[line for line in Path('/proc/stat').read_text().splitlines() if line.startswith('cpu')]
            row['sample_finished_ns']=time.monotonic_ns()
            stream.write(json.dumps(row)+'\n')
            if os.environ.get('TAIL_QMP')=='1' and qmp is not None and not snapshot_done and trigger.exists():
                from qmp_snapshot import snapshot
                host_stacks=[]
                for cpu in cpus:
                    tid=cpu['thread-id']
                    task_root=Path('/proc')/str(qemu)/'task'/str(tid)
                    state={'cpu_index':cpu['cpu-index'],'thread_id':tid,'read_start_ns':time.monotonic_ns()}
                    for name in ['stack','syscall','wchan']:
                        try:state[name]=(task_root/name).read_text()
                        except OSError as error:state[name]={'unavailable':str(error)}
                    state['read_end_ns']=time.monotonic_ns()
                    host_stacks.append(state)
                event={'type':'guest_snapshot','trigger':json.loads(trigger.read_text()),
                       'host_stacks':host_stacks,'snapshot':snapshot(qmp,cpus)}
                stream.write(json.dumps(event)+'\n')
                snapshot_done=True
            count+=1
            time.sleep(max(0,.01-(time.monotonic_ns()-before)/1e9))
        stream.write(json.dumps({'type':'clock_end', **clock_pair(), 'samples':count,
                                 'stop_requested':Path(stop).exists(),'snapshot_done':snapshot_done,
                                 'trigger_present':trigger.exists()})+'\n')
    if qmp is not None:qmp.close()


@contextlib.contextmanager
def case_trace(service, mode):
    if os.environ.get('TAIL_TRACE')!='1':
        yield {}
        return
    directory=Path(os.environ['PERF_SAMPLE_DIR'])
    prefix=directory/f'{service}-{mode}'
    stop=prefix.with_name(prefix.name+'-observer.stop')
    ready=prefix.with_name(prefix.name+'-observer.ready')
    output=prefix.with_name(prefix.name+'-host-timeline.jsonl')
    events=[]
    def gc_event(phase, info):
        events.append({'mono_ns':time.monotonic_ns(),'phase':phase,**info})
    proc=subprocess.Popen([sys.executable,__file__,'watch',str(output),str(stop),str(ready),
                           os.environ['PROBE_QEMU_OWNER'],str(os.getpid())])
    try:
        for _ in range(200):
            if ready.exists():break
            if proc.poll() is not None:raise RuntimeError('host timeline observer exited before readiness')
            time.sleep(.01)
        else:raise RuntimeError('host timeline observer did not become ready')
        gc.callbacks.append(gc_event)
        yield
    finally:
        if gc_event in gc.callbacks:gc.callbacks.remove(gc_event)
        stop.write_text('stop\n')
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate();proc.wait(timeout=5)
            raise RuntimeError('host timeline observer did not stop')
        prefix.with_name(prefix.name+'-client-gc.json').write_text(json.dumps(events)+'\n')
        if proc.returncode!=0:raise RuntimeError(f'host timeline observer failed: {proc.returncode}')
        final=json.loads(output.read_text().splitlines()[-1])
        assert final['type']=='clock_end' and final['stop_requested'] and final['samples']>0


@contextlib.contextmanager
def network_trace(service, mode, host, port, tools):
    if os.environ.get('TAIL_TRACE')!='1':
        yield
        return
    directory=Path(os.environ['PERF_SAMPLE_DIR'])
    prefix=directory/f'{service}-{mode}-fixed'
    route=json.loads(subprocess.check_output(['ip','-j','route','get',host],text=True))
    interface=route[0]['dev']
    capture_path=prefix.with_suffix('.pcap')
    command=[os.environ.get('TAIL_TCPDUMP',str(tools/'tcpdump')),'-Z','root','--immediate-mode','-U','-i',interface,'-n','-s','128','-B','4096','-w',str(capture_path),
             f'tcp and host {host} and port {port}']
    log=prefix.with_name(prefix.name+'-tcpdump.log')
    metadata={'command':command,'interface':interface,'server_port':port,'clock_start':clock_pair()}
    state={}
    with log.open('w') as stream:
        proc=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=stream)
        try:
            for _ in range(200):
                if 'listening on' in log.read_text():break
                if proc.poll() is not None:raise RuntimeError('tcpdump exited before readiness')
                time.sleep(.01)
            else:raise RuntimeError('tcpdump did not become ready')
            yield state
            records=state['requests']
            deadline=time.monotonic()+3
            while True:
                coverage=stream_coverage(capture_path,records,port,allow_partial=True)
                if coverage['complete'] or time.monotonic()>=deadline:break
                time.sleep(.01)
            metadata['wire_coverage']=coverage
            if not coverage['complete']:
                raise RuntimeError('packet capture does not cover every completed request and response')
        finally:
            if proc.poll() is None:proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill();proc.wait(timeout=5)
                raise RuntimeError('tcpdump did not stop')
            metadata.update(clock_end=clock_pair(),exit_code=proc.returncode)
            prefix.with_name(prefix.name+'-capture.json').write_text(json.dumps(metadata,indent=2)+'\n')
            if proc.returncode!=0 or tcpdump_drops(log.read_text())!=0:
                raise RuntimeError('packet capture failed or dropped packets')


if __name__=='__main__':
    assert sys.argv[1]=='watch'
    watch(*sys.argv[2:6],int(sys.argv[6]))
