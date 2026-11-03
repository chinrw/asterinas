# SPDX-License-Identifier: MPL-2.0
"""Offline correction for perf's completed, requested SIGINT shutdown."""
import copy
import re
import signal

from original_validate_profile import validate


def finalization(data, metadata, log):
    errors = []
    code = metadata.get('exit_code')
    signals = metadata.get('stop_signals', [])
    if type(code) is not int or code not in (0, -signal.SIGINT):
        errors.append('Exit is neither ordinary success nor direct SIGINT status -2')
    if code == -signal.SIGINT and signals != ['SIGINT']:
        errors.append('SIGINT exit was not requested exactly once without escalation')
    if signals not in ([], ['SIGINT']):
        errors.append('Stop signal sequence contains escalation or an unexpected signal')
    if metadata.get('unexpected_early_exit') is not False:
        errors.append('Recorder was not established to exit only at requested stop')
    if metadata.get('errors') != []:
        errors.append('Recorder metadata contains errors or has no explicit error list')
    names = ('start_requested_ns', 'spawned_ns', 'stop_requested_ns', 'exit_observed_ns', 'stop_finished_ns')
    timestamps = [metadata.get(name) for name in names]
    if not all(type(value) is int and value > 0 for value in timestamps) or timestamps != sorted(timestamps):
        errors.append('Recorder start/stop/exit timestamps are missing or incoherent')
    elif any(not timestamps[0] <= row['time_ns'] <= timestamps[3] for row in data['samples']):
        errors.append('Raw sample timestamp lies outside recorder start/observed-exit bounds')
    markers = [line for line in log.splitlines()
               if re.fullmatch(r'\[\s*perf record:\s*Captured and wrote\s+.+\]\s*', line)]
    if len(markers) != 1:
        errors.append('Expected exactly one perf Captured and wrote finalization marker')
    declared = None
    if len(markers) == 1:
        counts = re.findall(r'\((\d+) samples?\)', markers[0])
        if len(counts) > 1:
            errors.append('Ambiguous declared sample count in finalization marker')
        elif counts:
            declared = int(counts[0])
            if declared != len(data['samples']):
                errors.append('Finalization marker sample count differs from parsed records')
        elif re.search(r'\bsamples?\b', markers[0]):
            errors.append('Unrecognized declared sample count in finalization marker')
    return {'valid': not errors, 'errors': errors, 'original_exit_code': code,
            'intentional_sigint_candidate': code == -signal.SIGINT,
            'declared_samples': declared, 'parsed_samples': len(data['samples']),
            'finalization_markers': markers}


def corrected_validate(data, metadata, windows, ranges, log):
    gate = finalization(data, metadata, log)
    original = validate(data, metadata, windows, ranges)
    checked = copy.deepcopy(metadata)
    # Normalize only an independently finalized SIGINT in an ephemeral copy.
    if gate['valid'] and metadata['exit_code'] == -signal.SIGINT:
        checked['exit_code'] = 0
    report = validate(data, checked, windows, ranges)
    report['errors'] += [f'Finalization: {error}' for error in gate['errors']]
    report['valid'] = not report['errors']
    report.update(original_validation=original, finalization=gate,
        correction_applied=gate['valid'] and metadata['exit_code'] == -signal.SIGINT,
        scope='Completed-SIGINT validation gate. Original validator report and metadata remain unchanged; all original clock, identity, window, ELF, and loss gates remain.')
    return report
