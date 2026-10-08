#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parent
results = []

with tempfile.TemporaryDirectory() as temporary:
    fixtures = Path(temporary)
    for version in ['base', 'fixed']:
        source = (root / ('check.' + version)).read_text()
        branches = source[source.index('\t-e)'):source.index('\t-s)')]
        script = '''#!/bin/bash
exclude_tests=()
_fatal() { echo "$*" >&2; exit 1; }
while [ $# -gt 0 ]; do
case "$1" in
''' + branches + '''esac
shift
done
for entry in "${exclude_tests[@]}"; do
    [[ -z $entry ]] || printf "%s\\n" "$entry"
done
exit 0
'''
        (fixtures / version).write_text(script)

    def run(version, arguments, environment=None):
        return subprocess.run(
            [os.environ.get('LOADER_BASH', '/bin/bash'), str(fixtures / version), *arguments],
            env=environment, text=True, capture_output=True, timeout=5,
        )

    cases = [
        ('normal', 'generic/558\ngeneric/590\n', ['generic/558', 'generic/590']),
        ('comments', '# comment\ngeneric/558# comment\n', ['generic/558']),
        ('empty', '', []),
        ('only-comments', '# comment\n', []),
        ('no-final-newline', 'generic/558', ['generic/558']),
        ('blank-lines', '\ngeneric/558\n\n', ['generic/558']),
    ]
    for name, contents, expected in cases:
        path = fixtures / name
        path.write_text(contents)
        for version in ['base', 'fixed']:
            result = run(version, ['-E', str(path)])
            assert result.returncode == 0 and result.stdout.splitlines() == expected, (name, version, result)
        results.append({'case': name, 'result': 'same exclusions'})

    spaced = fixtures / 'spaced list'
    spaced.write_text('generic/558\n')
    before = run('base', ['-E', str(spaced)])
    after = run('fixed', ['-E', str(spaced)])
    assert before.returncode == 0 and not before.stdout
    assert after.returncode == 0 and after.stdout.splitlines() == ['generic/558']
    results.append({'case': 'quoted file path with spaces', 'result': 'baseline ignores it; fixed loads it'})

    first = fixtures / 'first'
    second = fixtures / 'second'
    first.write_text('generic/558\n')
    second.write_text('generic/590\n')
    for version in ['base', 'fixed']:
        result = run(version, ['-E', str(first), '-E', str(second)])
        assert result.returncode == 0 and result.stdout.splitlines() == ['generic/558', 'generic/590']
    results.append({'case': 'repeated -E', 'result': 'both append'})

    for version in ['base', 'fixed']:
        result = run(version, ['-E', str(fixtures / 'absent')])
        assert result.returncode == 0 and not result.stdout
    results.append({'case': 'absent file', 'result': 'existing ignore behavior retained'})

    shim_directory = fixtures / 'bin'
    shim_directory.mkdir()
    sed = shim_directory / 'sed'
    for partial in [False, True]:
        partial_output = 'printf "generic/558\\n"\n' if partial else ''
        sed.write_text('#!/bin/sh\n' + partial_output + 'exit 7\n')
        sed.chmod(0o755)
        environment = dict(os.environ, PATH=str(shim_directory) + ':' + os.environ['PATH'])
        before = run('base', ['-E', str(first)], environment)
        after = run('fixed', ['-E', str(first)], environment)
        assert before.returncode == 0 and after.returncode == 1 and not after.stdout
        assert 'Failed to read exclusion file' in after.stderr
        results.append({
            'case': 'producer failure' + (' after partial output' if partial else ''),
            'baseline_status': before.returncode,
            'fixed_status': after.returncode,
        })

    # Run only in a disposable container with its own /dev, never on the host.
    descriptor_alias = Path('/dev/fd')
    original = os.readlink(descriptor_alias)
    try:
        descriptor_alias.unlink()
        before = run('base', ['-E', str(first)])
        after = run('fixed', ['-E', str(first)])
        assert before.returncode == 0 and not before.stdout and '/dev/fd/' in before.stderr
        assert after.returncode == 0 and after.stdout.splitlines() == ['generic/558'] and not after.stderr
        results.append({
            'case': 'missing /dev/fd with -E',
            'result': 'baseline loses exclusions; fixed loads them',
        })

        other = run('fixed', ['-e', 'generic/558'])
        assert other.returncode == 0 and not other.stdout and '/dev/fd/' in other.stderr
        results.append({'case': 'missing /dev/fd with unchanged -e', 'result': 'still loses exclusions'})
    finally:
        descriptor_alias.symlink_to(original)

print(json.dumps(results, indent=2))
