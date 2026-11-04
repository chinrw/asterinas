# SPDX-License-Identifier: MPL-2.0
"""Reuse the fixed-rate Redis workload with a per-window output file."""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from host_clients import CLIENT_HOST, REDIS_PORT, fixed_rate, ready

case_id = int(sys.argv[1])
ready()
with socket.create_connection((CLIENT_HOST, REDIS_PORT), timeout=3) as channel:
    channel.sendall(b'*3\r\n$3\r\nSET\r\n$14\r\nfixed-perf-key\r\n$3\r\nabc\r\n')
    if channel.recv(64) != b'+OK\r\n':
        raise ValueError('Redis fixture SET failed')
destination = Path(os.environ['PERF_SAMPLE_DIR']) / f'census-redis-{case_id}-fixed.json'
result = asyncio.run(fixed_rate('redis', 1000, 5, destination))
print('CENSUS_REDIS ' + json.dumps({key: value for key, value in result.items() if key != 'records'}), flush=True)
