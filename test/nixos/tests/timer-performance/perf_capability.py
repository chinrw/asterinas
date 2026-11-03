#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Bounded, owned-QEMU perf capability probe; never modifies host settings."""
import argparse
import collections
import json
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import time

MARKER = b'PERF_FIXTURE_READY\n'


def boot_sector():
    # Normalize CS before emitting the readiness marker through the debug port.
    code = bytearray(b'\xea\x05\x7c\x00\x00')
    for byte in MARKER:
        code.extend((0xb0, byte, 0xe6, 0xe9))  # mov al, byte; out 0xe9, al.
    code.append(0xfa)  # CLI makes the loop independent of BIOS interrupt handlers.
    loop_rip = 0x7c00 + len(code)
    code.extend(b'\xeb\xfe')
    return bytes(code) + bytes(510 - len(code)) + b'\x55\xaa', loop_rip


def stop(proc):
    # The unreaped process remains ours, so its process group cannot be reused.
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            proc.wait(timeout=3)
            return
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=3)


def run(argv, out, name, timeout=20):
    (out / (name + '.command.json')).write_text(json.dumps(argv))
    with (out / (name + '.stdout')).open('wb') as stdout, (out / (name + '.stderr')).open('wb') as stderr:
        proc = subprocess.Popen(argv, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124
        finally:
            stop(proc)


def inspect(path, loop_rip):
    data = path.read_bytes()
    if data[:8] != b'PERFILE2':
        raise ValueError('Only little-endian regular perf.data is supported')
    attr_size, attrs_offset, attrs_size, offset, size = struct.unpack_from('<5Q', data, 16)
    if attrs_size != attr_size or attr_size < 48:
        raise ValueError('Expected exactly one event attribute')
    sample_type = struct.unpack_from('<Q', data, attrs_offset + 24)[0]
    if not sample_type & 1:
        raise ValueError('PERF_SAMPLE_IP is absent')
    end = offset + size
    if end > len(data):
        raise ValueError('Truncated data section')
    counts = collections.Counter()
    guest_ips = collections.Counter()
    lost_records = lost_events = lost_sample_records = lost_samples = 0
    while offset < end:
        kind, misc, length = struct.unpack_from('<IHH', data, offset)
        if length < 8 or offset + length > end:
            raise ValueError('Invalid record length')
        if kind == 2:  # PERF_RECORD_LOST: id, lost.
            if length < 24:
                raise ValueError('Truncated PERF_RECORD_LOST')
            lost_records += 1
            lost_events += struct.unpack_from('<Q', data, offset + 16)[0]
        elif kind == 13:  # PERF_RECORD_LOST_SAMPLES: lost.
            if length < 16:
                raise ValueError('Truncated PERF_RECORD_LOST_SAMPLES')
            lost_sample_records += 1
            lost_samples += struct.unpack_from('<Q', data, offset + 8)[0]
        elif kind == 9:  # PERF_RECORD_SAMPLE; CPUMODE is misc & 7.
            context = misc & 7
            counts[str(context)] += 1
            ip_offset = offset + 8 + (8 if sample_type & (1 << 16) else 0)
            if ip_offset + 8 > offset + length:
                raise ValueError('Truncated sample IP')
            ip = struct.unpack_from('<Q', data, ip_offset)[0]
            if context in (4, 5):  # GUEST_KERNEL / GUEST_USER.
                guest_ips[hex(ip)] += 1
        offset += length
    hits = guest_ips[hex(loop_rip)]
    return {'sample_type': hex(sample_type), 'context_counts': dict(counts),
            'guest_ips': dict(guest_ips.most_common(20)), 'guest_loop_samples': hits,
            'lost_records': lost_records, 'lost_events': lost_events,
            'lost_sample_records': lost_sample_records, 'lost_samples': lost_samples,
            'guest_ip_verified': hits >= 3}


def terminate(_signum, _frame):
    raise SystemExit(143)


def main():
    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    out = args.out.resolve()
    boot = out / 'boot.img'
    sector, loop_rip = boot_sector()
    boot.write_bytes(sector)
    if args.self_test:
        assert len(boot.read_bytes()) == 512
        proc = subprocess.Popen(['sleep', '30'], start_new_session=True)
        stop(proc)
        assert proc.returncode is not None
        print('Boot sector and owned-process cleanup passed; perf/KVM not exercised.')
        return 0
    result = {'guest_ip_verified': False, 'expected_loop_rip': hex(loop_rip),
              'ready_marker_seen': False}
    try:
        perf = os.environ.get('TAIL_PERF_BIN', shutil.which('perf') or 'perf')
        qemu = shutil.which('qemu-system-x86_64') or 'qemu-system-x86_64'
        for name, argv in [('uname', ['uname', '-a']), ('perf-version', [perf, '--version']),
                           ('qemu-version', [qemu, '--version']), ('perf-kvm-help', [perf, 'kvm', '-h']),
                           ('perf-list', [perf, 'list'])]:
            run(argv, out, name)
        (out / 'perf_event_paranoid').write_text(Path('/proc/sys/kernel/perf_event_paranoid').read_text())
        argv = [qemu, '-accel', 'kvm', '-cpu', 'host', '-smp', '1', '-m', '64M',
                '-drive', f'file={boot},format=raw,readonly=on,if=ide', '-boot', 'c',
                '-nic', 'none', '-display', 'none', '-serial', 'none', '-monitor', 'none',
                '-debugcon', f'file:{out / "debugcon.log"}',
                '-global', 'isa-debugcon.iobase=0xe9', '-no-reboot', '-no-shutdown']
        (out / 'qemu.command.json').write_text(json.dumps(argv))
        with (out / 'qemu.log').open('wb') as log:
            proc = subprocess.Popen(argv, stdout=log, stderr=log, start_new_session=True)
            try:
                (out / 'qemu.identity').write_text(Path(f'/proc/{proc.pid}/stat').read_text())
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise RuntimeError('KVM QEMU exited before readiness; inspect qemu.log')
                    debugcon = out / 'debugcon.log'
                    if debugcon.exists() and MARKER in debugcon.read_bytes():
                        result['ready_marker_seen'] = True
                        break
                    time.sleep(0.1)
                if not result['ready_marker_seen']:
                    raise RuntimeError('Boot readiness marker absent after 15 seconds')
                result['cycles_stat_exit'] = run([perf, 'stat', '-e', 'cycles', '-p', str(proc.pid),
                                                  '--', 'sleep', '1'], out, 'cycles-stat', timeout=5)
                result['events'] = {}
                events = ['cpu-clock']
                if result['cycles_stat_exit'] == 0:
                    events.append('cycles')
                else:
                    result['events']['cycles'] = {
                        'guest_ip_verified': False,
                        'skipped_reason': 'cycles stat failed; inspect cycles-stat.stderr'}
                for event in events:
                    event_result = {'guest_ip_verified': False}
                    result['events'][event] = event_result
                    data_path = out / f'{event}.perf.data'
                    try:
                        if proc.poll() is not None:
                            raise RuntimeError('Owned QEMU exited before event recording')
                        event_result['record_exit'] = run([
                            perf, 'kvm', '--host', '--guest', 'record',
                            '-e', event, '-F', '99', '-p', str(proc.pid),
                            '-o', str(data_path), '--', 'sleep', '7'],
                            out, event + '-record', timeout=12)
                        event_result['qemu_alive_after_record'] = proc.poll() is None
                        if data_path.exists() and event_result['record_exit'] == 0:
                            event_result['raw_dump_exit'] = run([
                                perf, 'script', '-i', str(data_path), '-D'],
                                out, event + '-raw-dump', timeout=5)
                            event_result.update(inspect(data_path, loop_rip))
                            event_result['guest_ip_verified'] &= event_result['qemu_alive_after_record']
                        if not event_result['guest_ip_verified']:
                            event_result['error'] = 'Guest RIP threshold not established for this event'
                    except Exception as error:
                        event_result['error'] = f'{type(error).__name__}: {error}'
                result['verified_events'] = [event for event, event_result in result['events'].items()
                                             if event_result['guest_ip_verified']]
                result['guest_ip_verified'] = bool(result['verified_events'])
                if not result['guest_ip_verified']:
                    raise RuntimeError('Guest instruction-pointer capability unverified')
            finally:
                stop(proc)
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['guest_ip_verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
