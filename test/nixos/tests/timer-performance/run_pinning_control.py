# SPDX-License-Identifier: MPL-2.0
"""Five fresh-guest order pairs with identical userland and HTTP/Redis history."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import random

SUITE=Path(__file__).resolve().parent
REPO=SUITE.parents[3]
OUT=REPO/'target/timer-affinity-control'
OUT.mkdir(exist_ok=True,parents=True)
assert not any((OUT/name).exists() for name in ['plan.json','samples.jsonl','summary.json'])
qemu_version=subprocess.check_output(['qemu-system-x86_64','--version'],text=True)
assert qemu_version.splitlines()[0]=='QEMU emulator version 10.2.1'
required={'PERF_PILOT':'0','PERF_NETDEV':'tap','NETDEV':'tap','SMP':'4','MEM':'8G',
    'ENABLE_KVM':'1','RELEASE_LTO':'1','QEMU_DISPLAY':'none','TAIL_TRACE':'1',
    'TAIL_QMP':'map','TAIL_REQUIRE_SCHEDSTATS':'1','TAIL_PERF':'0','TAIL_BATCH_BUILD':'0',
    'TAIL_BATCH':'0','TAIL_HISTORY_CHURN':'1','TAIL_HTTP_HISTORY':'10000','TAIL_REDIS_SECONDS':'20'}
for key,value in required.items():assert os.environ.get(key)==value,(key,os.environ.get(key))
assert os.environ['VHOST']=='on'
assert os.environ.get('OVMF')=='on' and 'OVMF=on' in os.environ.get('MAKEFLAGS','')
assert Path('/proc/sys/kernel/sched_schedstats').read_text().strip()=='1'
assert len(os.sched_getaffinity(0))==4
host_clients=(OUT/'host-clients-path').read_text().strip().splitlines()[-1]
os.environ.update(PERF_HOST_CLIENTS=host_clients,TAIL_TCPDUMP=str(Path(host_clients)/'bin/tcpdump'),
    HISTORY_QEMU_SELECTOR=str(SUITE/'qemu_select.py'),HISTORY_CLIENT_SCRIPT=str(SUITE/'history_client.py'),
    TMPDIR=str(OUT/'tmp'),PINNING_CONTROL='1')
(OUT/'tmp').mkdir(exist_ok=True)
import paired_pinning_control as paired
import trace_validation
from affinity_control import validate_affinity
validate_traces=trace_validation.validate_traces
source=OUT/'retirement'
assert (source/'clean.img').is_file() and not (source/'clean.img').is_symlink()
helper_hash=(OUT/'helper-sha256.txt').read_text().strip()
assert helper_hash=='4c3b4b19275bda043431f531e703f996145bae2f399c756a64d7cbde735817a3'

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
metadata={'qemu_version':qemu_version,'paired_blocks':5,'order_seed':7,
    'timer_helper_sha256':helper_hash,'shared_clean_image':str(source/'clean.img'),
    'shared_clean_image_sha256':sha(source/'clean.img'),
    'asterinas_candidate':'64848d3e0a23ff0903f4be485fb7750c1709a575',
    'asterinas_elf_sha256':sha(source/'kernel.elf'),'asterinas_boot_image_sha256':sha(source/'boot-kernel.bin'),
    'build':json.loads((OUT/'build-metadata.json').read_text()),
    'run_settings':{key:os.environ[key] for key in [*required,'VHOST','OVMF','MAKEFLAGS']},
    'actual_host_cpu':(OUT/'host-cpu.txt').read_text(),'host_allowed_logical_cpus':sorted(os.sched_getaffinity(0)),
    'scope':'Five paired blocks on one CI host. Plain repair only, identical regular master userland image and unchanged history workload. Only four owned QMP vCPU TID masks change in the pinned arm; no other task affinity, scheduler policy, perf, or kernel instrumentation. Distinct host logical CPUs are not physical-core isolation.'}
(OUT/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')

def validate(directory,item):
    phases=[json.loads(x) for x in (directory/'phases.jsonl').read_text().splitlines()]
    expected={'identity','services_start','client_ready_none','load_start_churn_history',
        'begin_churn_history','client_history_redis_none','end_churn_history','load_stop_churn_history','services_stop'}
    expected.add('control_logging')
    assert len(phases)==len(expected) and {r['name'] for r in phases}==expected
    assert all(r['passed'] is True and type(r['host_elapsed_ns']) is int and r['host_elapsed_ns']>0
        and type(r['qemu_cpu_ticks']) is int and r['qemu_cpu_ticks']>=0 for r in phases)
    assert json.loads((directory/'go.json').read_text())['outcome']=='pass'
    log=(directory/'qemu.log').read_text(errors='replace')
    loads=[json.loads(m[1]) for m in re.finditer(r'(?m)^PERF_LOAD_VALIDATED (\{[^\r\n]+\})\r?$',log)]
    assert len(loads)==1 and loads[0]['achieved_cycles_per_second']>=4500
    names=re.findall(r'(?m)^CONTROL_KERNEL ([^\r\n]+)\r?$',log)
    assert len(names)==1
    assert names[0].startswith('Linux 5.13.0 ')
    history=json.loads((directory/'http-history.json').read_text());assert history['http_requests']==10000 and history['exit_code']==0
    fixed=json.loads((directory/'redis-churn-fixed.json').read_text());assert fixed['planned']==fixed['completed']==len(fixed['records'])==20000
    assert fixed['service']=='redis' and fixed['offered_rps']==1000 and fixed['duration_s']==20
    assert {row['id'] for row in fixed['records']}==set(range(20000))
    for row in fixed['records']:
        assert row['error'] is None and row['scheduled_ns']<=row['started_ns']<=row['finished_ns']
        assert row['latency_ns']==row['finished_ns']-row['scheduled_ns']
    closed=json.loads((directory/'redis-churn.json').read_text());assert closed['exit_code']==0 and closed['requests_each']==100000
    assert closed['service']=='redis' and closed['mode']=='churn'
    assert all(type(closed[k]) in (int,float) and closed[k]>0 for k in ['set_rps','get_rps'])
    validate_traces(directory,[('redis','churn')])
    timeline=[json.loads(line) for line in (directory/'redis-churn-host-timeline.jsonl').read_text().splitlines()]
    first=timeline[0];pid=int((directory/'qemu.owner').read_text().split()[0]);mapping=first['cpu_map']
    assert first['qemu']==pid and first['qmp_enabled'] and first['qmp_mode']=='map'
    assert len(mapping)==4 and {r['cpu-index'] for r in mapping}==set(range(4))
    assert len({r['thread-id'] for r in mapping})==4 and all(type(r['thread-id']) is int and r['thread-id']>0 for r in mapping)
    observed={t['pid'] for row in timeline if row['type']=='sample' for t in row['qemu_tasks']}
    assert {r['thread-id'] for r in mapping}<=observed
    assert not any(row['type']=='guest_snapshot' for row in timeline)
    assert first['sched_schedstats']=='1'
    assert not list(directory.glob('*-batch-control.json'))
    assert not (directory/'guest-perf.data').exists()
    argv=json.loads((directory/'qemu.argv.json').read_text())
    assert '-bios' in argv and argv[argv.index('-bios')+1].endswith('/OVMF.fd')
    assert argv[argv.index('-smp')+1]=='4' and argv[argv.index('-m')+1]=='8G'
    assert argv[argv.index('-netdev')+1].startswith('tap,')
    assert 'vhost='+os.environ['VHOST'] in argv[argv.index('-netdev')+1]
    assert not any(flag in argv for flag in ['-kernel','-initrd','-append'])
    affinity=validate_affinity(directory,item)
    return {'phases':len(phases),'fixed_requests':20000,'http_history_requests':10000,
        'achieved_producer_rate':loads[0]['achieved_cycles_per_second'],'guest_uname':names[0],'affinity':affinity}
paired.validate_performance=validate
rng=random.Random(7)
plan=[]
for block in range(5):
    conditions=[False,True];rng.shuffle(conditions)
    for pin in conditions:
        plan.append({'id':len(plan),'variant':'retirement','os':'asterinas','cpus':4,'block':block,
                     'pilot':False,'batch_enabled':False,'http_requests':10000,'pin_vcpus':pin})
(OUT/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
rows=[]
try:
    for item in plan:
        row=paired.sample(item,helper_hash)
        rows.append(row)
        with (OUT/'samples.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
        if not row['cleanup_ok']:
            raise RuntimeError('cleanup failed; refusing next guest')
finally:
    after=sha(source/'clean.img')
    master_unchanged=after==metadata['shared_clean_image_sha256']
    result={'shared_clean_image_after_sha256':after,'master_unchanged':master_unchanged,'planned':len(plan),'observed':len(rows),'unattempted':plan[len(rows):],
        'complete':master_unchanged and len(rows)==len(plan) and all(r['complete'] and r['cleanup_ok'] for r in rows),
        'scope':metadata['scope']}
    (OUT/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print('AFFINITY_HISTORY_SUMMARY',json.dumps(result),flush=True)
raise SystemExit(0 if result['complete'] else 1)
