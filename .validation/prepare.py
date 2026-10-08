#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
fixed = Path(sys.argv[1]).resolve()
fixtures = root / 'test/initramfs/src/conformance/xfstests/loader_validation'
fixtures.mkdir(exist_ok=True)
baseline = fixtures / 'check.baseline'
subprocess.run(['patch', '--batch', '--reverse', '--output', str(baseline), str(fixed),
                str(root / 'test/initramfs/nix/conformance/xfstests-exclusions.patch')], check=True)
shutil.copyfile(fixed, root / '.validation/check.fixed')
shutil.copyfile(baseline, root / '.validation/check.base')
shutil.copyfile(root / '.validation/guest.sh', fixtures / 'guest.sh')
(fixtures / 'guest.sh').chmod(0o755)
baseline.chmod(0o755)
identity = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in
            [('packaged_check', fixed), ('reconstructed_baseline_check', baseline)]}
identity['packaged_check_path'] = str(fixed)
identity['bash'] = fixed.read_text().splitlines()[0][2:]
(root / '.validation/source-identity.json').write_text(json.dumps(identity, indent=2)+'\n')
print(json.dumps(identity, indent=2))
