# SPDX-License-Identifier: MPL-2.0
"""Export recoverable frozen raw snapshots only after all measured windows."""
import base64
import gzip
import hashlib
from pathlib import Path
import time

for path in sorted(Path('/var/tmp').glob('timer-census-*.bin'), key=lambda item: int(item.stem.rsplit('-', 1)[1])):
    case_id = int(path.stem.rsplit('-', 1)[1])
    raw = path.read_bytes()
    if len(raw) != 327808:
        raise ValueError(f'invalid snapshot length: {path}')
    compressed = gzip.compress(raw, mtime=0)
    encoded = base64.b64encode(compressed).decode('ascii')
    chunks = [encoded[index:index+768] for index in range(0, len(encoded), 768)]
    print(f"CENSUS_BLOB_BEGIN {case_id} {len(raw)} {hashlib.sha256(raw).hexdigest()} {len(compressed)} {hashlib.sha256(compressed).hexdigest()} {len(chunks)}", flush=True)
    for index, chunk in enumerate(chunks, 1):
        print(f'CENSUS_BLOB_CHUNK {case_id} {index} {chunk}', flush=True)
        time.sleep(.01)
    print(f'CENSUS_BLOB_END {case_id}', flush=True)
print('CENSUS_EXPORT_DONE', flush=True)
