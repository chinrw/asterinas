#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Run bounded loopback transactions using the installed candidate clients."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import sys
import time

phase = sys.argv[1]
evidence = Path('/evidence') / phase
evidence.mkdir(exist_ok=True)
(evidence / 'executed-harness-sha256.txt').write_text(hashlib.sha256(Path(__file__).read_bytes()).hexdigest() + '\n')
selected = json.loads(Path('/evidence/selected-clients.json').read_text())
commands = []
results = {}
binaries = {}

def run(args, timeout=45, filename=None, check=True, env=None):
    print('+', shlex.join(map(str, args)), flush=True)
    record = {'args': list(map(str, args)), 'timeout_seconds': timeout}
    commands.append(record)
    try:
        completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, timeout=timeout, env=env)
        record['exit_code'] = completed.returncode
        output = completed.stdout
    except subprocess.TimeoutExpired as error:
        record['exit_code'] = 124
        output = (error.stdout or b'').decode(errors='replace') if isinstance(error.stdout, bytes) else (error.stdout or '')
        output += '\nTIMEOUT\n'
    if filename:
        (evidence / filename).write_text(output)
    print(output, end='', flush=True)
    if check and record['exit_code']:
        raise RuntimeError(f"command returned {record['exit_code']}: {shlex.join(map(str, args))}")
    return output

def wait_port(port):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.2):
                return
        except OSError:
            time.sleep(.1)
    raise RuntimeError(f'port {port} did not become ready')

def wait_listening_port(port):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        listeners = Path('/proc/net/tcp').read_text().splitlines()[1:]
        if any(int(row.split()[1].split(':')[1], 16) == port and row.split()[3] == '0A' for row in listeners):
            return
        time.sleep(.1)
    raise RuntimeError(f'port {port} did not become ready')

def start_daemon(args, filename, env):
    print('+ daemon:', shlex.join(args), flush=True)
    record = {'args': args, 'timeout_seconds': 10, 'stdout': filename}
    commands.append(record)
    # The server forks and keeps its output descriptors open. A regular file
    # lets us wait for the starter without waiting for the daemon to exit.
    with (evidence / filename).open('w') as log:
        completed = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=10, env=env)
    record['exit_code'] = completed.returncode
    if completed.returncode:
        raise RuntimeError(f'daemon starter returned {completed.returncode}')

@contextlib.contextmanager
def server(args, filename, port):
    print('+ server:', shlex.join(map(str, args)), flush=True)
    with (evidence / filename).open('w') as log:
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        commands.append({'server_args': list(map(str, args)), 'pid': process.pid})
        try:
            wait_port(port)
            if process.poll() is not None:
                raise RuntimeError(f'server exited with {process.returncode}')
            yield
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

def verify_binaries():
    packages = {'ab': 'apacheHttpd', 'iperf3': 'iperf3', 'memaslap': 'libmemcached',
                'lat_tcp': 'lmbench', 'bw_tcp': 'lmbench',
                'redis-benchmark': 'redis', 'redis-cli': 'redis', 'redis-server': 'redis'}
    binary_info = {}
    for binary, package in packages.items():
        path = Path('/root/.nix-profile/bin') / binary
        resolved = path.resolve(strict=True)
        expected = Path(selected[package]['outputPath']) / 'bin' / binary
        if resolved != expected.resolve(strict=True):
            raise RuntimeError(f'{binary} resolved to {resolved}, expected {expected}')
        binaries[binary] = str(path)
        header = run(['readelf', '-h', str(resolved)], filename=f'{binary}-elf-header.txt')
        machine = re.search(r'^\s*Machine:\s*(.*)$', header, re.M).group(1)
        expected_machine = {'x86_64': 'Advanced Micro Devices X86-64', 'aarch64': 'AArch64'}[os.uname().machine]
        if machine != expected_machine:
            raise RuntimeError(f'{binary} ELF machine is {machine}, expected {expected_machine}')
        interpreter = run(['readelf', '-l', str(resolved)], filename=f'{binary}-elf-program-headers.txt')
        run(['ldd', str(resolved)], filename=f'{binary}-ldd.txt')
        if 'not found' in (evidence / f'{binary}-ldd.txt').read_text():
            raise RuntimeError(f'{binary} has a missing shared library')
        binary_info[binary] = {'profile_path': str(path), 'real_path': str(resolved), 'machine': machine}
    (evidence / 'binary-paths.json').write_text(json.dumps(binary_info, indent=2) + '\n')
    for package, value in selected.items():
        if not Path(value['outputPath']).is_dir():
            raise RuntimeError(f'{package} output was removed')
        run(['nix-store', '--query', '--roots', value['outputPath']], filename=f'{package}-gc-roots.txt')

def http():
    webroot = evidence / 'http-root'
    webroot.mkdir(exist_ok=True)
    (webroot / 'index.html').write_text('host-client-validation\n')
    with server(['python3', '-m', 'http.server', '18080', '--bind', '127.0.0.1', '--directory', str(webroot)], 'http-server.log', 18080):
        output = run([binaries['ab'], '-n', '30', '-c', '2', 'http://127.0.0.1:18080/index.html'], filename='ab.log')
        assert re.search(r'Complete requests:\s+30\b', output)
        assert re.search(r'Failed requests:\s+0\b', output)

def iperf():
    with server([binaries['iperf3'], '-s', '-B', '127.0.0.1', '-p', '15201'], 'iperf3-server.log', 15201):
        output = run([binaries['iperf3'], '-c', '127.0.0.1', '-p', '15201', '-t', '2', '-J'], filename='iperf3.json')
        data = json.loads(output)
        assert 'error' not in data
        assert data['end']['sum_received']['bytes'] > 0

def memcached():
    with server([os.environ['VALIDATION_MEMCACHED_SERVER'], '-l', '127.0.0.1', '-p', '11211', '-U', '0', '-u', 'root', '-m', '32', '-t', '2'], 'memcached-server.log', 11211):
        output = run([binaries['memaslap'], '-s', '127.0.0.1:11211', '-t', '2s', '-T', '1', '-c', '2', '-w', '1k', '-S', '1s'], filename='memaslap.log')
        assert re.search(r'Ops:\s*[1-9][0-9]*\b', output)
        assert re.search(r'TPS:\s*[1-9][0-9]*(?:\.[0-9]+)?\b', output)
        assert not re.search(r'(?:get|set|conn|cmd) (?:failures|errors)\s*:\s*[1-9]', output, re.I)
        assert re.search(r'get_misses:\s*0\b', output)
        assert re.search(r'cmd_get:\s*[1-9][0-9]*\b', output)
        assert re.search(r'cmd_set:\s*[1-9][0-9]*\b', output)

def lmbench(binary, port, extra):
    environment = dict(os.environ)
    environment.update({'LMBENCH_TCP_LAT_PORT': '31234', 'LMBENCH_TCP_BW_PORT': '31236'})
    try:
        start_daemon([binaries[binary], '-s', '127.0.0.1', '-b', '1'], f'{binary}-server.log', environment)
        # An empty TCP connection is lat_tcp's shutdown request. Inspect the
        # listener instead of opening a connection during readiness checks.
        wait_listening_port(port)
        output = run([binaries[binary], *extra, '-P', '1', '-N', '2', '127.0.0.1'], timeout=90, filename=f'{binary}.log', env=environment)
        assert re.search(r'\d+(?:\.\d+)?', output)
        if binary == 'lat_tcp':
            assert 'microseconds' in output
        else:
            assert 'MB/sec' in output
    finally:
        run([binaries[binary], '-S', '127.0.0.1'], filename=f'{binary}-shutdown.log', check=False, env=environment)
        time.sleep(.2)
        # lmbench daemonizes its server, so explicitly reap any survivors before GC.
        run(['pkill', '-TERM', '-x', binary], filename=f'{binary}-cleanup.log', check=False)

def redis():
    with server([binaries['redis-server'], '--bind', '127.0.0.1', '--port', '16379', '--save', '', '--appendonly', 'no', '--dir', '/tmp'], 'redis-server.log', 16379):
        assert run([binaries['redis-cli'], '-p', '16379', 'PING'], filename='redis-ping.log').strip() == 'PONG'
        output = run([binaries['redis-benchmark'], '-h', '127.0.0.1', '-p', '16379', '-n', '10000', '-c', '2', '-t', 'ping_inline,ping_mbulk,set,get'], filename='redis-benchmark.log')
        for label in ['PING_INLINE', 'PING_MBULK', 'SET', 'GET']:
            assert f'====== {label} ======' in output
        assert len(re.findall(r'10000 requests completed in', output)) == 4
        assert len(re.findall(r'throughput summary:\s*[1-9][0-9]*(?:\.[0-9]+)? requests per second', output)) == 4
        assert int(run([binaries['redis-cli'], '-p', '16379', 'DBSIZE'], filename='redis-dbsize.log').strip()) > 0

try:
    verify_binaries()
    results['installed-paths-and-elf'] = {'status': 'passed'}
except Exception as error:
    results['installed-paths-and-elf'] = {'status': 'failed', 'error': repr(error)}

for name, operation in [('apacheHttpd', http), ('iperf3', iperf), ('libmemcached', memcached),
                        ('lmbench-lat_tcp', lambda: lmbench('lat_tcp', 31234, [])),
                        ('lmbench-bw_tcp', lambda: lmbench('bw_tcp', 31236, ['-m', '65536'])), ('redis', redis)]:
    try:
        operation()
        results[name] = {'status': 'passed'}
    except Exception as error:
        results[name] = {'status': 'failed', 'error': repr(error)}
    print(name, results[name], flush=True)

(evidence / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
(evidence / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
sys.exit(int(any(result['status'] != 'passed' for result in results.values())))
