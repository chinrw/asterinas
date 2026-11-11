#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Keep explicit timer tests separate from the standard ktest selection."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess

repo = Path.cwd()
support = repo / '.github/timer-core'
output = repo / 'target/timer-core-ci'
output.mkdir(parents=True, exist_ok=False)
names = json.loads((support / 'selection.json').read_text())
environment = dict(os.environ,
    TIMER_CORE_REAL_CARGO=shutil.which('cargo'),
    TIMER_CORE_LOCK=str(support / 'Cargo.lock'),
    PATH=str(support / 'bin') + ':' + os.environ['PATH'])
cpus = int(environment['SMP'])
records = []
for index, name in enumerate(names):
    setup = repo / 'target/osdk/linux-bzimage-setup'
    if setup.exists():
        shutil.move(str(setup), str(output / f'setup-before-{index}'))
    command = ['cargo', 'osdk', 'test', name, '--profile', 'release-lto',
        f'--initramfs={repo}/test/initramfs/build/initramfs.cpio.gz',
        '--grub-mkrescue=' + shutil.which('grub-mkrescue'),
        '--grub-boot-protocol=linux', '--kcmd-args=earlycon',
        '--kcmd-args=console=ttyS0', '--qemu-args=-accel kvm']
    log = output / f'{index}-{name.rsplit("::", 1)[-1]}.log'
    with log.open('w') as stream:
        with subprocess.Popen(command, cwd=repo / 'kernel/core', env=environment,
                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True) as process:
            try:
                exit_code = process.wait(timeout=1200)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                exit_code = 124
    text = re.sub(r'\x1b\[[0-9;]*m', '', log.read_text(errors='replace'))
    print(text, flush=True)
    executed = f'test {name} ...' in text
    green = bool(re.search(r'test result: ok\. 1 passed; 0 failed; \d+ filtered out', text))
    processors = sorted({int(cpu) for cpu in re.findall(r'Processor (\d+) started', text)})
    expected = exit_code == 0 and executed and green and processors == list(range(1, cpus))
    records.append(dict(test=name, cpus=cpus, exit=exit_code,
        actually_executed=executed, expected_result=expected, processors=processors,
        result_lines=re.findall(r'test result: [^\r\n]+', text),
        timer_source_sha256=hashlib.sha256((repo / 'kernel/core/src/time/core/timer.rs').read_bytes()).hexdigest(),
        extra_sources={name: hashlib.sha256((repo/name).read_bytes()).hexdigest() for name in ("ostd/libs/timer-queue/src/lib.rs","ostd/libs/timer-queue/Cargo.toml","kernel/core/Cargo.toml")},
        queue_source_sha256=hashlib.sha256((repo / 'kernel/core/src/time/core/timer/queue.rs').read_bytes()).hexdigest()))
    (output / 'results.json').write_text(json.dumps(records, indent=2)+'\n')
    assert expected, records[-1]
