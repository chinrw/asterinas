# SPDX-License-Identifier: MPL-2.0
"""Exercise evidence rejection without a guest or kernel instrumentation."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import decode

ABI = json.loads(decode.ABI_PATH.read_text())
NAMES = {field['name']: field['index'] for field in ABI['fields']}


def blob(mode=2, values=None, active=0):
    words = [0] * (ABI['blob_bytes'] // 8)
    words[:16] = [ABI['magic'], ABI['version'], 16, 64, 5, 128, 10**9, 1, 1, mode, 10, 20, 30, active, 0, 0]
    for name, value in (values or {}).items():
        words[16 + NAMES[name]] = value
    return struct.pack('<' + 'Q' * len(words), *words)


def export(data):
    compressed = gzip.compress(data, mtime=0)
    encoded = base64.b64encode(compressed).decode()
    chunks = [encoded[index:index+128] for index in range(0, len(encoded), 128)]
    lines = [f'CENSUS_BLOB_BEGIN 1 {len(data)} {hashlib.sha256(data).hexdigest()} {len(compressed)} {hashlib.sha256(compressed).hexdigest()} {len(chunks)}']
    lines += [f'CENSUS_BLOB_CHUNK 1 {index} {chunk}' for index, chunk in enumerate(chunks, 1)]
    lines += ['CENSUS_BLOB_END 1', 'CENSUS_EXPORT_DONE']
    return lines


class EvidenceValidation(unittest.TestCase):
    def test_valid_round_trip(self):
        raw = blob()
        self.assertEqual(decode.recover('\n'.join(export(raw))), {1: raw})
        self.assertTrue(decode.decode(raw, ABI, 1)['timing_clean'])

    def test_duplicate_chunk(self):
        lines = export(blob())
        lines.insert(2, lines[1])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            decode.recover('\n'.join(lines))

    def test_duplicate_begin(self):
        lines = export(blob())
        lines.insert(1, lines[0])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            decode.recover('\n'.join(lines))

    def test_missing_chunk(self):
        lines = export(blob())
        del lines[1]
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            decode.recover('\n'.join(lines))

    def test_bad_hash(self):
        lines = export(blob())
        words = lines[0].split()
        words[5] = '0' * 64
        lines[0] = ' '.join(words)
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            decode.recover('\n'.join(lines))

    def test_nonquiescent(self):
        with self.assertRaisesRegex(ValueError, 'quiescent'):
            decode.decode(blob(active=1), ABI)

    def test_bad_minimum_probe(self):
        with self.assertRaisesRegex(ValueError, 'minimum probe'):
            decode.decode(blob(values={'min_probe': 1}), ABI)

    def test_periodic_partition(self):
        with self.assertRaisesRegex(ValueError, 'periodic retirement partition'):
            decode.decode(blob(values={'periodic_retire_intent': 1}), ABI)

    def test_periodic_subset(self):
        with self.assertRaisesRegex(ValueError, 'periodic subset'):
            decode.decode(blob(values={'periodic_retire_intent': 1, 'periodic_retire_miss': 1}), ABI)

    def test_mode_zero_and_one(self):
        with self.assertRaisesRegex(ValueError, 'gate-off'):
            decode.decode(blob(mode=0, values={'insert': 1}), ABI)
        with self.assertRaisesRegex(ValueError, 'counter-only'):
            decode.decode(blob(mode=1, values={'insert_tsc_sum': 1}), ABI)

    def test_missing_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / 'serial.log'
            log.write_text('\n'.join(export(blob())))
            (root / 'census-plan.json').write_text(json.dumps(dict(plain=False, windows=[dict(case_id=1), dict(case_id=2)])))
            result = subprocess.run([sys.executable, str(Path(decode.__file__)), str(log), str(root/'decoded'), '--sample-dir', str(root)], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('exported case set differs from plan', result.stderr)

    def test_backward_tsc_preserves_raw_and_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = blob(values={'tsc_backwards': 1})
            log = root / 'serial.log'
            log.write_text('\n'.join(export(raw)))
            output = root / 'decoded'
            result = subprocess.run([sys.executable, str(Path(decode.__file__)), str(log), str(output)], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((output/'census-1.bin').read_bytes(), raw)
            self.assertFalse(json.loads((output/'summary.json').read_text())['validation_passed'])
            self.assertIn('timing quality failed', result.stderr)


if __name__ == '__main__':
    unittest.main()
