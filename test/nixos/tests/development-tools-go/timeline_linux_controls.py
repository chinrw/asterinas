# SPDX-License-Identifier: MPL-2.0
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

suite=Path(__file__).resolve().parent;repo=suite.parents[3]
root=repo/'target/notingo-native-controls/timeline';root.mkdir(parents=True,exist_ok=True)
def build(attribute):
    expression=f'(import {suite}/timeline_helpers.nix {{pkgs=import {repo}/distro/nixpkgs.nix {{}};}}).{attribute}'
    return Path(subprocess.check_output(['nix-build','--no-out-link','--expr',expression],text=True).strip().splitlines()[-1])
folders={name:build(name) for name in ['original','sleep20','tools']}
os.sched_setaffinity(0,{min(os.sched_getaffinity(0))});env=dict(os.environ,GOMAXPROCS='1')
for name in ['GODEBUG','GOTRACEBACK']:env.pop(name,None)
rows=[];digests=[]
for name,attribute in [('tagged','original'),('tagged20','sleep20')]:
    binary=folders[attribute]/'bin/minimal-cgo';digest=hashlib.sha256(binary.read_bytes()).hexdigest()
    digests.append(name+' '+digest);shutil.copy2(binary,root/(name+'-helper'))
    for iteration in range(1,5):
        start=time.monotonic();r=subprocess.run(['timeout','--signal=KILL','10s',str(binary),'poll'],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        tags=re.findall(r'(?m)^TIMELINE_SYSMON_TID (\d+)\s*$',r.stdout)
        row={'name':name,'iteration':iteration,'seconds':time.monotonic()-start,'exit_code':r.returncode,'sysmon_tags':tags,'passed':r.returncode==0 and len(tags)==1 and 'BARRIER_OK' in r.stdout};rows.append(row)
        (root/f'linux-{name}-{iteration}.log').write_text(r.stdout);print('LINUX_TIMELINE_CONTROL '+json.dumps(row),flush=True)
for ns in [1000,20000]:
    r=subprocess.run(['timeout','--signal=KILL','30s',str(folders['tools']/'bin/linux-sleep-control'),str(ns)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    (root/f'linux-sleep-{ns}.log').write_text(r.stdout);assert r.returncode==0,r.stdout
    print(r.stdout,flush=True)
(root/'digests.txt').write_text('\n'.join(digests)+'\n');(root/'linux-results.json').write_text(json.dumps(rows,indent=2)+'\n')
assert all(r['passed'] for r in rows)
