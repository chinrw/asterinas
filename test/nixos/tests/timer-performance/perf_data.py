# SPDX-License-Identifier: MPL-2.0
"""Decode the deliberately restricted, one-event guest profiling format."""
import argparse
import json
from pathlib import Path
import struct

ALLOWED = sum(1 << bit for bit in (0, 1, 2, 6, 7, 8, 9, 16))
REQUIRED = sum(1 << bit for bit in (0, 1, 2, 7, 8))


def parse(path):
    data = Path(path).read_bytes()
    if len(data) < 104 or data[:8] != b'PERFILE2':
        raise ValueError('Expected regular little-endian perf.data')
    attr_size, attr_offset, attr_bytes, offset, length = struct.unpack_from('<5Q', data, 16)
    if attr_bytes != attr_size or attr_size < 112 or attr_offset + attr_bytes > len(data):
        raise ValueError('Expected one complete event attribute')
    attr_version_size = struct.unpack_from('<I', data, attr_offset + 4)[0]
    if attr_version_size < 96 or attr_version_size > attr_size - 16:
        raise ValueError('Unsupported perf_event_attr size')
    sample_type = struct.unpack_from('<Q', data, attr_offset + 24)[0]
    if sample_type & ~ALLOWED or sample_type & REQUIRED != REQUIRED:
        raise ValueError(f'Unsupported or missing sample fields: {sample_type:#x}')
    flags = struct.unpack_from('<Q', data, attr_offset + 40)[0]
    clockid = struct.unpack_from('<i', data, attr_offset + 92)[0]
    result = {'sample_type': hex(sample_type), 'clockid': clockid,
              'use_clockid': bool(flags & (1 << 25)), 'samples': [],
              'lost_records': 0, 'lost_events': 0, 'lost_sample_records': 0,
              'lost_samples': 0}
    end = offset + length
    if end > len(data):
        raise ValueError('Truncated data section')
    while offset < end:
        if offset + 8 > end:
            raise ValueError('Truncated record header')
        kind, misc, size = struct.unpack_from('<IHH', data, offset)
        if size < 8 or offset + size > end:
            raise ValueError('Invalid record size')
        pos = offset + 8
        record_end = offset + size

        def take(fmt):
            nonlocal pos
            width = struct.calcsize(fmt)
            if pos + width > record_end:
                raise ValueError('Truncated record payload')
            value = struct.unpack_from(fmt, data, pos)
            pos += width
            return value

        if kind == 9:
            row = {'context': misc & 7}
            if sample_type & (1 << 16):
                row['identifier'] = take('<Q')[0]
            row['ip'] = hex(take('<Q')[0])
            row['pid'], row['tid'] = take('<II')
            row['time_ns'] = take('<Q')[0]
            if sample_type & (1 << 6):
                row['id'] = take('<Q')[0]
            if sample_type & (1 << 9):
                row['stream_id'] = take('<Q')[0]
            row['cpu'], reserved = take('<II')
            row['period'] = take('<Q')[0]
            if pos != record_end:
                raise ValueError('Unexpected sample payload bytes')
            result['samples'].append(row)
        elif kind == 2:
            _, lost = take('<QQ')
            result['lost_records'] += 1
            result['lost_events'] += lost
        elif kind == 13:
            result['lost_sample_records'] += 1
            result['lost_samples'] += take('<Q')[0]
        offset = record_end
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('data')
    args = parser.parse_args()
    print(json.dumps(parse(args.data)))
