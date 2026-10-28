# SPDX-License-Identifier: MPL-2.0
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

suite = Path(__file__).resolve().parent
repo = suite.parents[3]
out = repo / 'target/notingo-native-controls/fresh-benchmark'
expressions = {
    'minimal': 'import ./test/nixos/tests/development-tools-go/minimal_helper.nix { pkgs = import ./distro/nixpkgs.nix {}; }',
    'sleep20': '(import ./test/nixos/tests/development-tools-go/foreign_wait_helpers.nix { pkgs = import ./distro/nixpkgs.nix {}; }).sleep20',
    'sleep1000': '(import ./test/nixos/tests/development-tools-go/foreign_wait_helpers.nix { pkgs = import ./distro/nixpkgs.nix {}; }).sleep1000',
}
helpers = {}
for name, expr in expressions.items():
    path = subprocess.check_output(['nix-build','--no-out-link','--expr',expr],cwd=repo,text=True).strip().splitlines()[-1]
    helpers[name] = Path(path)/'bin/minimal-cgo'
digests = {name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in helpers.items()}
assert digests['minimal'] == json.loads((suite/'minimal_layout.json').read_text())['sha256']
(out/'helper-sha256.json').write_text(json.dumps(digests,indent=2)+'\n')
os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
env = dict(os.environ,GOMAXPROCS='1')
for key in ['GODEBUG','GOTRACEBACK']:
    env.pop(key,None)
rows=[]
for name,path in helpers.items():
    begin=time.monotonic()
    result=subprocess.run([str(path),'poll'],env=env,capture_output=True,text=True,timeout=5)
    elapsed=time.monotonic()-begin
    row={'helper':name,'sha256':digests[name],'seconds':elapsed,'exit_code':result.returncode,'passed':result.returncode==0 and result.stdout.strip()=='BARRIER_OK' and elapsed<=3}
    rows.append(row)
    (out/f'native-{name}.log').write_text(result.stdout+result.stderr)
    print('FRESH_NATIVE '+json.dumps(row),flush=True)
(out/'native.json').write_text(json.dumps(rows,indent=2)+'\n')
assert all(r['passed'] for r in rows)
