# SPDX-License-Identifier: MPL-2.0
"""Run paired fresh guests without publishing results or killing unrelated VMs."""
import hashlib
import json
import os
from pathlib import Path
import random
import signal
import socket
import subprocess
import time
from validation import validate_go, validate_performance

SUITE=Path(__file__).resolve().parent
REPO=SUITE.parents[3]
OUT=REPO/'target/timer-performance'

def free_ports():
    sockets=[]
    try:
        for _ in range(2):
            s=socket.socket();s.bind(('127.0.0.1',0));sockets.append(s)
        return [s.getsockname()[1] for s in sockets]
    finally:
        for s in sockets:s.close()

def sample(item, helper_hash):
    directory=OUT/f"sample-{item['id']:03d}-{item['variant']}-smp{item['cpus']}"
    directory.mkdir()
    subprocess.run(['cp','--reflink=auto','--sparse=always',str(OUT/item['variant']/'clean.img'),str(REPO/'target/nixos/asterinas.img')],check=True)
    with (REPO/'target/nixos/asterinas.img').open('rb') as image:
        image_hash=hashlib.file_digest(image,'sha256').hexdigest()
    expected_image=(OUT/item['variant']/'artifact.sha256').read_text().splitlines()[2].split()[0]
    assert image_hash==expected_image,'copied image identity mismatch'
    (directory/'image-sha256.txt').write_text(image_hash+'\n')
    transport=os.environ.get('PERF_NETDEV','user')
    assert transport in {'user','tap'}
    nginx,redis=free_ports() if transport=='user' else (8080,6379)
    client_host='127.0.0.1' if transport=='user' else '10.0.2.15'
    original_interfaces={p.name for p in Path('/sys/class/net').iterdir()}
    (directory/'network-before.json').write_text(json.dumps({'interfaces':sorted(original_interfaces),'routes':subprocess.check_output(['ip','-j','route'],text=True)},indent=2)+'\n')
    owner=directory/'qemu.owner'
    exit_file=directory/'driver.exit'
    environment=dict(os.environ,SMP=str(item['cpus']),MEM='8G',NETDEV=transport,QEMU_DISPLAY='none',PERF_CLIENT_HOST=client_host,
        NGINX_PORT=str(nginx),REDIS_PORT=str(redis),PERF_SAMPLE_DIR=str(directory),PERF_BLOCK=str(item['block']),
        PERF_HELPER_SHA256=helper_hash,PROBE_QEMU_OWNER=str(owner),PROBE_SAMPLE_ID=str(item['id']),
        PROBE_QEMU_CONTROL=str(SUITE/'owned_qemu.py'),QEMU_BIN=str(SUITE/'owned_qemu.py'),
        PROBE_HOST_SAMPLE='minimal',PROBE_HOST_MARKER=str(directory/'go.json'),
        PROBE_HELPER_SHA256='1a6b8c133239c4b7792709d25bb4bb4b5eded497e79f1d36e2a62066df9a623d')
    command=['bash','-c','make run_nixos NIXOS_TEST_SUITE=timer-performance NIXOS_TEST_CASE=lifecycle_performance NIXOS_TEST_TIMEOUT=150s; result=$?; printf "%s\\n" "$result" > "$1"; exec sleep 86400','perf-supervisor',str(exit_file)]
    started=time.monotonic();record={**item,'complete':False,'reason':'outer_timeout'}
    with (directory/'driver.log').open('w') as log:
        process=subprocess.Popen(command,cwd=REPO,env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            while time.monotonic()-started<900:
                if (directory/'complete').exists() and (directory/'go.json').exists():
                    go=json.loads((directory/'go.json').read_text())
                    validate_go(go,item,owner)
                    record.update(complete=True,reason='finished',go=go)
                    break
                if exit_file.exists() or process.poll() is not None:
                    record['reason']='driver_exited_without_complete_records';break
                time.sleep(.1)
        except Exception as error:
            record.update(complete=False,reason='validation_exception',error=f'{type(error).__name__}: {error}')
        finally:
            cleanup=True
            if owner.exists():
                result=subprocess.run(['python3',str(SUITE/'owned_qemu.py'),'--stop',str(owner)],capture_output=True,text=True)
                (directory/'cleanup.log').write_text(result.stdout+result.stderr)
                cleanup=result.returncode==0
            if process.poll() is None:os.killpg(process.pid,signal.SIGKILL)
            process.wait(timeout=5)
            record['cleanup_ok']=cleanup
    if transport=='tap':
        for _ in range(300):
            remaining={p.name for p in Path('/sys/class/net').iterdir()}-original_interfaces
            if not remaining:break
            time.sleep(.01)
        record['remaining_interfaces']=sorted(remaining)
        record['cleanup_ok']=record['cleanup_ok'] and not remaining
    record['transport']=transport
    record['client_host']=client_host
    record['host_elapsed_s']=time.monotonic()-started
    record['host_loadavg']=Path('/proc/loadavg').read_text().strip()
    for name in ['qemu.log','qemu-serial.log']:
        path=REPO/name
        if path.exists():path.replace(directory/name)
    if record['complete']:
        try:
            record['validated_inventory']=validate_performance(directory,item)
        except Exception as error:
            record.update(complete=False,reason='performance_validation',error=f'{type(error).__name__}: {error}')
    (directory/'record.json').write_text(json.dumps(record,indent=2)+'\n')
    print('PERF_SAMPLE '+json.dumps(record),flush=True)
    if not record['cleanup_ok']:raise RuntimeError('cleanup failed; refusing next guest')
    return record

def main():
    OUT.mkdir(exist_ok=True,parents=True)
    # Build the same guest helper with the pinned compiler for identity checking.
    expr='import ./test/nixos/tests/timer-performance/timer_helper.nix { pkgs=import ./distro/nixpkgs.nix {}; }'
    built=subprocess.check_output(['nix-build','--no-out-link','--expr',expr],cwd=REPO,text=True).strip().splitlines()[-1]
    binary=Path(built)/'bin/timer-perf'
    digest=hashlib.sha256(binary.read_bytes()).hexdigest()
    (OUT/'helper-sha256.txt').write_text(digest+'\n')
    rng=random.Random(20261029)
    pilot=os.environ.get('PERF_PILOT')=='1'
    plan=[]
    cpu_settings=[int(x) for x in os.environ.get('PERF_CPUS','1,4').split(',')]
    assert cpu_settings and set(cpu_settings)<={1,4} and len(cpu_settings)==len(set(cpu_settings))
    for cpus in cpu_settings:
        for block in range(1 if pilot else 5):
            variants=['base','retirement'];rng.shuffle(variants)
            for variant in variants:plan.append({'id':len(plan),'cpus':cpus,'block':block,'variant':variant,'pilot':pilot})
    (OUT/'plan.json').write_text(json.dumps({'seed':20261029,'samples':plan},indent=2)+'\n')
    rows=[]
    for item in plan:
        row=sample(item,digest);rows.append(row)
        with (OUT/'samples.jsonl').open('a') as stream:stream.write(json.dumps(row)+'\n')
        if not row['complete']:break
    complete=len(rows)==len(plan) and all(r['complete'] and r['cleanup_ok'] for r in rows)
    candidate_rows=[r for r in rows if r['variant']=='retirement']
    fixed_go=all(r.get('go',{}).get('outcome')=='pass' for r in candidate_rows) if candidate_rows else None
    summary={'planned':len(plan),'observed':len(rows),'unattempted':[p for p in plan if p['id'] not in {r['id'] for r in rows}],
             'complete':complete,'candidate_go_passed':fixed_go,'pilot':pilot}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('PERF_SUMMARY '+json.dumps(summary),flush=True)
    return 0 if complete and fixed_go else 1

if __name__=='__main__':raise SystemExit(main())
