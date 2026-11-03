# SPDX-License-Identifier: MPL-2.0
"""Require the single existing dependency edge needed for safe diagnostic hooks."""
import copy
import hashlib
from pathlib import Path
import sys
import tomllib
original,diagnostic=map(Path,sys.argv[1:])
assert hashlib.sha256(original.read_bytes()).hexdigest()=='e8588a8b8146949e286f1f9ae5636685b82f5ed2ec0721fe9194d226ff23b750'
a=tomllib.loads(original.read_text());b=tomllib.loads(diagnostic.read_text());expected=copy.deepcopy(a)
entry=next(p for p in expected['package'] if p['name']=='osdk-heap-allocator')
assert entry['dependencies']==['log','ostd'];entry['dependencies'].append('spin')
assert b==expected, 'unexpected diagnostic dependency drift'
