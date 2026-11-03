#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Inject the same observer into either exact frozen timer source, without committing."""
import argparse
import hashlib
from pathlib import Path

HASHES = {
    '5d336540c27cfaed372e9d891ccd4eda4b8b587f059a5620fea4b8f1133feb4b': 'heap',
    '3bc1d7207d0fb040962a525bebefc1c14f9f053dc1d864627d085e0bd394a0ba': 'tree',
}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'expected one injection anchor: {old!r}')
    return text.replace(old, new, 1)


def apply(root):
    timer = root/'kernel/core/src/time/core/timer.rs'
    original = timer.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if digest not in HASHES:
        raise ValueError(f'timer source is not an exact frozen variant: {digest}')
    variant = HASHES[digest]
    text = original.decode()
    start = text.index('    pub(crate) fn process_expired_timers(&self) {')
    end = text.index('    /// Creates an [`Timer`]', start)
    body = text[start:end]
    body = once(body, '        let callbacks = {', """        // The recorder owns no timer references and takes no control lock here.
        let mut batch = crate::batch_probe::Batch::begin(self as *const Self as u64);
        if let Some(batch) = batch.as_mut() {
            let kind = u64::from(Arc::as_ptr(&self.clock) as *const ()
                == Arc::as_ptr(crate::time::clocks::MonotonicClock::get()) as *const ());
            batch.set_kind(kind);
        }
        let (callbacks, current_time) = {""")
    body = once(body, '            let mut timeout_list = self.timer_callbacks.disable_irq().lock();', '''            if let Some(batch) = batch.as_mut() { batch.before_queue(); }
            let mut timeout_list = self.timer_callbacks.disable_irq().lock();
            if let Some(batch) = batch.as_mut() { batch.acquired(timeout_list.len()); }''')
    body = once(body, '            if timeout_list.is_empty() {\n                return;', '''            if timeout_list.is_empty() {
                if let Some(batch) = batch.as_mut() { batch.collected(0); }
                return;''')
    head = 't' if variant == 'heap' else 'timer_callback'
    body = once(body, f'                if {head}.is_cancelled() {{', f'''                if {head}.is_cancelled() {{
                    if let Some(batch) = batch.as_mut() {{
                        batch.cancelled();
                    }}''')
    push = ('                    callbacks.push(timeout_list.pop().unwrap());' if variant == 'heap'
            else '                    callbacks.push(timeout_list.pop_first().unwrap().1);')
    body = once(body, push, f'''                    if let Some(batch) = batch.as_mut() {{ batch.selected(); }}
{push}''')
    body = once(body, '            callbacks\n        };', '''            if let Some(batch) = batch.as_mut() { batch.collected(timeout_list.len()); }
            (callbacks, current_time)
        };''')
    body = once(body, '        for callback in callbacks {', '''        if let Some(batch) = batch.as_mut() { batch.callbacks(); }
        for callback in callbacks {''')
    body = once(body, '            callback.call();', """            let outcome = callback.call();
            if let Some(batch) = batch.as_mut() {
                let late = if outcome == 2 {
                    current_time.saturating_sub(callback.expired_time)
                        .as_nanos().min(u128::from(u64::MAX)) as u64
                } else { 0 };
                batch.outcome(outcome, late);
            }""")
    instrumented = text[:start] + body + text[end:]
    call_start = instrumented.index('    fn call(&self) {')
    call_end = instrumented.index('\n    }', call_start) + len('\n    }')
    call = instrumented[call_start:call_end]
    call = once(call, '    fn call(&self) {', '    fn call(&self) -> u8 {')
    call = once(call, 'let Some(timer) = self.timer.upgrade() else {\n            return;',
                'let Some(timer) = self.timer.upgrade() else {\n            return 0;')
    call = once(call, '// The callback is cancelled.\n            return;',
                '// The callback is cancelled.\n            return 1;')
    call = once(call, '        (timer.registered_callback)(timer_guard);',
                '        (timer.registered_callback)(timer_guard);\n        2')
    instrumented = instrumented[:call_start] + call + instrumented[call_end:]
    lib = root/'kernel/core/src/lib.rs'
    lib_text = once(lib.read_text(), 'pub mod context;', 'mod batch_probe;\n\npub mod context;')
    prctl = root/'kernel/core/src/syscall/prctl.rs'
    prctl_text = once(prctl.read_text(), '    let prctl_cmd = PrctlCmd::from_args(option, arg2, arg3, arg4, arg5)?;', '''    if option == crate::batch_probe::CONTROL {
        return crate::batch_probe::control(arg2, arg3, arg4, arg5, ctx)
            .map(SyscallReturn::Return);
    }
    let prctl_cmd = PrctlCmd::from_args(option, arg2, arg3, arg4, arg5)?;''')
    target = root/'kernel/core/src/batch_probe.rs'
    if target.exists() or 'mod batch_probe;' in lib.read_text():
        raise ValueError('batch probe already exists; restore plain diagnostic inputs first')
    # Resolve and validate every anchor before the first write.
    source = Path(__file__).with_name('batch_probe.rs').read_bytes()
    target.write_bytes(source)
    timer.write_text(instrumented)
    lib.write_text(lib_text)
    prctl.write_text(prctl_text)
    print(f'injected {variant} sha256={digest}; no commits created')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout', type=Path)
    args = parser.parse_args()
    apply(args.checkout.resolve())
