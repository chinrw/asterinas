# SPDX-License-Identifier: MPL-2.0
"""Record one owned QEMU lifespan without taking ownership of QEMU cleanup."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def start_ticks(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()[19]


class GuestProfile:
    def __init__(self, directory, perf):
        self.directory = Path(directory)
        self.perf = str(perf)
        self.proc = None
        self.log = None
        self.meta = {'enabled': True, 'event': 'cpu-clock', 'frequency_hz': 99,
                     'clock': 'CLOCK_MONOTONIC', 'attempted': False,
                     'unexpected_early_exit': False, 'errors': []}

    def save(self):
        (self.directory / 'guest-perf.json').write_text(json.dumps(self.meta, indent=2) + '\n')

    def poll_start(self, owner):
        """Call each supervisor poll; attempt once when the atomic owner appears."""
        if self.meta['attempted'] or not Path(owner).exists():
            self.poll()
            return
        self.meta['attempted'] = True
        try:
            pid_text, expected, sample = Path(owner).read_text().split()
            pid = int(pid_text)
            fd = os.pidfd_open(pid)
            try:
                if start_ticks(pid) != expected:
                    raise RuntimeError('Owned QEMU identity changed before perf attach')
                self.meta['qemu'] = {'pid': pid, 'start_ticks': expected, 'sample': sample}
                self.meta['qemu_stat_at_start'] = Path(f'/proc/{pid}/stat').read_text()
                command = [self.perf, 'kvm', '--host', '--guest', 'record',
                           '-e', 'cpu-clock', '-F', '99', '--clockid', 'mono',
                           '--sample-cpu', '-p', str(pid), '-o',
                           str(self.directory / 'guest-perf.data')]
                # Default inheritance includes future QEMU threads. Never pass -i.
                self.meta['command'] = command
                self.meta['start_requested_ns'] = time.monotonic_ns()
                self.log = (self.directory / 'guest-perf.log').open('wb')
                self.proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                    stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
                self.meta['recorder_pid'] = self.proc.pid
                self.meta['spawned_ns'] = time.monotonic_ns()
            finally:
                os.close(fd)
        except Exception as error:
            self.meta['errors'].append(f'start: {type(error).__name__}: {error}')
        self.save()

    def poll(self):
        if self.proc is not None and self.proc.poll() is not None and 'exit_code' not in self.meta:
            self.meta.update(exit_code=self.proc.returncode,
                             exit_observed_ns=time.monotonic_ns(), unexpected_early_exit=True)

    def stop(self):
        """Never raise into the caller's QEMU cleanup; retain errors instead."""
        try:
            self.poll()
            self.meta['stop_requested_ns'] = time.monotonic_ns()
            if self.proc is not None and self.proc.poll() is None:
                for sig, seconds in ((signal.SIGINT, 5), (signal.SIGTERM, 2), (signal.SIGKILL, 2)):
                    try:
                        os.killpg(self.proc.pid, sig)
                    except ProcessLookupError:
                        pass
                    self.meta.setdefault('stop_signals', []).append(sig.name)
                    try:
                        self.proc.wait(timeout=seconds)
                        break
                    except subprocess.TimeoutExpired:
                        self.meta['errors'].append(f'recorder did not exit after {sig.name}')
                self.meta['exit_code'] = self.proc.poll()
                self.meta['exit_observed_ns'] = time.monotonic_ns()
            identity = self.meta.get('qemu')
            if identity:
                try:
                    self.meta['qemu_identity_matches_at_stop'] = start_ticks(identity['pid']) == identity['start_ticks']
                except (FileNotFoundError, ProcessLookupError):
                    self.meta['qemu_dead_at_stop'] = True
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
                # Even an unwritable evidence directory must not suppress VM cleanup.
                self.meta['errors'].append(f'evidence: {type(error).__name__}: {error}')
        return self.meta
