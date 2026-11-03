# SPDX-License-Identifier: MPL-2.0
"""Record owned, QMP-identified vCPU TIDs without transient QEMU workers."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time


def task_identity(pid, tid):
    root = Path(f'/proc/{pid}/task/{tid}')
    fields = (root / 'stat').read_text().rsplit(') ', 1)[1].split()
    return {'tid': tid, 'start_ticks': fields[19], 'comm': (root / 'comm').read_text().strip()}


def query_vcpus(path, deadline=None):
    query_deadline = time.monotonic() + 2
    deadline = min(query_deadline, deadline) if deadline is not None else query_deadline
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('Owned QMP query exceeded two seconds')
        return value
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(remaining())
        sock.connect(str(path))
        buffer = bytearray()
        received = messages = 0
        def read():
            nonlocal received, messages
            while True:
                remaining()
                newline = buffer.find(b'\n')
                if newline >= 0:
                    if newline > 65536:
                        raise ValueError('QMP reply line exceeds 64 KiB')
                    line = bytes(buffer[:newline])
                    del buffer[:newline + 1]
                    messages += 1
                    if messages > 256:
                        raise ValueError('QMP query exceeded 256 messages')
                    return json.loads(line)
                if len(buffer) > 65536:
                    raise ValueError('QMP reply line exceeds 64 KiB')
                sock.settimeout(remaining())
                chunk = sock.recv(min(4096, 65537 - len(buffer)))
                if not chunk:
                    raise RuntimeError('Owned QMP disconnected')
                received += len(chunk)
                if received > 1048576:
                    raise ValueError('QMP query exceeded 1 MiB of reply data')
                buffer.extend(chunk)
        if 'QMP' not in read():
            raise ValueError('Missing QMP greeting')
        for identifier, command in enumerate(('qmp_capabilities', 'query-cpus-fast'), 1):
            sock.settimeout(remaining())
            sock.sendall(json.dumps({'execute': command, 'id': identifier}).encode() + b'\n')
            while True:
                reply = read()
                if reply.get('id') != identifier:
                    continue
                if 'error' in reply:
                    raise RuntimeError(f'QMP {command}: {reply["error"]}')
                if identifier == 2:
                    return reply['return']
                break


class GuestProfile:
    def __init__(self, directory, perf, expected_cpus=4, query_cpus=query_vcpus):
        self.directory = Path(directory)
        self.perf = str(perf)
        self.expected_cpus = expected_cpus
        self.query_cpus = query_cpus
        self.proc = None
        self.log = None
        self.meta = {'enabled': True, 'event': 'cpu-clock', 'frequency_hz': 99,
                     'expected_cpus': expected_cpus,
                     'clock': 'CLOCK_MONOTONIC', 'attempted': False,
                     'target_scope': 'Owned QMP vCPU TIDs only; no inheritance or I/O workers',
                     'unexpected_early_exit': False, 'errors': []}

    def save(self):
        (self.directory / 'guest-perf.json').write_text(json.dumps(self.meta, indent=2) + '\n')

    def poll_start(self, owner):
        """Retry bounded QMP readiness without delaying workloads by a cooldown."""
        if self.meta['attempted'] or not Path(owner).exists():
            self.poll()
            return
        now = time.monotonic_ns()
        self.meta.setdefault('readiness_started_ns', now)
        try:
            readiness_deadline = self.meta['readiness_started_ns'] / 1e9 + 30
            if time.monotonic() >= readiness_deadline:
                raise TimeoutError('Owned vCPU readiness exceeded 30 seconds')
            pid_text, expected, sample = Path(owner).read_text().split()
            pid = int(pid_text)
            fd = os.pidfd_open(pid)
            try:
                if task_identity(pid, pid)['start_ticks'] != expected:
                    raise RuntimeError('Owned QEMU identity changed before attachment')
                path = Path(owner).with_suffix('.qmp')
                if not path.exists():
                    raise FileNotFoundError('Owned QMP socket not yet present')
                mapping = (self.query_cpus(path, deadline=readiness_deadline)
                           if self.query_cpus is query_vcpus else self.query_cpus(path))
                tids = [entry['thread-id'] for entry in mapping]
                indexes = [entry['cpu-index'] for entry in mapping]
                if (len(tids) != self.expected_cpus or len(set(tids)) != len(tids)
                        or any(type(tid) is not int or tid <= 0 for tid in tids)
                        or any(type(index) is not int for index in indexes)
                        or set(indexes) != set(range(self.expected_cpus))):
                    raise ValueError('QMP has not exposed the complete unique vCPU thread set')
                targets = [{**task_identity(pid, entry['thread-id']),
                            'qemu_cpu_index': entry['cpu-index']} for entry in mapping]
                if task_identity(pid, pid)['start_ticks'] != expected:
                    raise RuntimeError('Owned QEMU identity changed during QMP query')
                self.meta.update(attempted=True, qemu={'pid': pid, 'start_ticks': expected, 'sample': sample},
                    qmp_cpu_map=mapping, target_threads=targets, qmp_ready_ns=time.monotonic_ns(),
                    qemu_stat_at_start=Path(f'/proc/{pid}/stat').read_text())
                command = [self.perf, 'kvm', '--host', '--guest', 'record',
                           '-e', 'cpu-clock', '-F', '99', '--clockid', 'mono', '--sample-cpu',
                           '-t', ','.join(str(tid) for tid in sorted(tids)), '--no-inherit',
                           '-o', str(self.directory / 'guest-perf.data')]
                self.meta['command'] = command
                self.meta['start_requested_ns'] = time.monotonic_ns()
                self.log = (self.directory / 'guest-perf.log').open('wb')
                self.proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                    stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
                self.meta.update(recorder_pid=self.proc.pid, spawned_ns=time.monotonic_ns())
            finally:
                os.close(fd)
        except Exception as error:
            self.meta['last_readiness_error'] = f'{type(error).__name__}: {error}'
            if self.meta['attempted'] or now - self.meta['readiness_started_ns'] >= 30_000_000_000:
                self.meta['attempted'] = True
                self.meta['errors'].append(self.meta['last_readiness_error'])
        self.save()
        if self.proc is not None and not self.meta['errors']:
            self.poll()
            if not self.meta['unexpected_early_exit']:
                self.meta['ready_released_ns'] = time.monotonic_ns()
                self.save()
                temporary = self.directory / 'guest-perf-ready.tmp'
                temporary.write_text(json.dumps({'recorder_pid': self.proc.pid,
                    'target_threads': self.meta['target_threads']}) + '\n')
                temporary.replace(self.directory / 'guest-perf-ready')
        if self.meta['attempted'] and (self.meta['errors'] or self.meta['unexpected_early_exit']):
            (self.directory / 'guest-perf-failed').write_text(json.dumps(self.meta) + '\n')

    def reap(self):
        if self.proc is None or self.proc.returncode is not None:
            return self.proc.returncode if self.proc else None
        pid, status, usage = os.wait4(self.proc.pid, os.WNOHANG)
        if pid:
            self.proc.returncode = os.waitstatus_to_exitcode(status)
            self.meta['recorder_usage'] = {'user_seconds': usage.ru_utime,
                'system_seconds': usage.ru_stime, 'max_rss_kib': usage.ru_maxrss,
                'voluntary_context_switches': usage.ru_nvcsw,
                'involuntary_context_switches': usage.ru_nivcsw,
                'scope': 'wait4 usage of this exact owned recorder child'}
        return self.proc.returncode

    def poll(self):
        code = self.reap()
        if self.proc is not None and code is not None and 'exit_code' not in self.meta:
            self.meta.update(exit_code=code, exit_observed_ns=time.monotonic_ns(), unexpected_early_exit=True)

    def stop(self):
        try:
            self.poll()
            self.meta['stop_requested_ns'] = time.monotonic_ns()
            if self.proc is not None and self.proc.returncode is None:
                try:
                    fields = Path(f'/proc/{self.proc.pid}/stat').read_text().rsplit(') ', 1)[1].split()
                    self.meta['recorder_stat_before_stop'] = {
                        'observed_ns': time.monotonic_ns(), 'start_ticks': fields[19],
                        'user_ticks': int(fields[11]), 'system_ticks': int(fields[12]),
                        'clk_tck': os.sysconf('SC_CLK_TCK')}
                except (FileNotFoundError, ProcessLookupError):
                    pass
            if self.proc is not None and self.reap() is None:
                for sig, seconds in ((signal.SIGINT, 5), (signal.SIGTERM, 2), (signal.SIGKILL, 2)):
                    try:
                        os.killpg(self.proc.pid, sig)
                    except ProcessLookupError:
                        pass
                    self.meta.setdefault('stop_signals', []).append(sig.name)
                    deadline = time.monotonic() + seconds
                    while self.reap() is None and time.monotonic() < deadline:
                        time.sleep(.01)
                    if self.reap() is not None:
                        break
                    self.meta['errors'].append(f'recorder did not exit after {sig.name}')
                self.meta.update(exit_code=self.reap(), exit_observed_ns=time.monotonic_ns())
            identity = self.meta.get('qemu')
            if identity:
                try:
                    self.meta['qemu_identity_matches_at_stop'] = task_identity(identity['pid'], identity['pid'])['start_ticks'] == identity['start_ticks']
                    self.meta['target_threads_at_stop'] = [task_identity(identity['pid'], target['tid'])
                        for target in self.meta['target_threads']]
                    if any(actual['start_ticks'] != expected['start_ticks'] for actual, expected in zip(
                            self.meta['target_threads_at_stop'], self.meta['target_threads'])):
                        self.meta['errors'].append('Owned vCPU thread identity changed')
                except (FileNotFoundError, ProcessLookupError):
                    self.meta['qemu_or_target_dead_at_stop'] = True
                    try:
                        if task_identity(identity['pid'], identity['pid'])['start_ticks'] == identity['start_ticks']:
                            self.meta['errors'].append('A selected vCPU TID vanished while owned QEMU remained alive')
                    except (FileNotFoundError, ProcessLookupError):
                        pass
        except Exception as error:
            self.meta['errors'].append(f'stop: {type(error).__name__}: {error}')
        finally:
            self.meta['stop_finished_ns'] = time.monotonic_ns()
            try:
                if self.log is not None:
                    self.log.close()
                for name in ('guest-perf.data', 'guest-perf.log'):
                    path = self.directory / name
                    if path.exists():
                        path.chmod(0o644)
                self.save()
            except Exception as error:
                self.meta['errors'].append(f'evidence: {type(error).__name__}: {error}')
        return self.meta
