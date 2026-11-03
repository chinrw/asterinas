#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Inject v3 into exact frozen timer and heap sources, without committing."""
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


PRISTINE = {'osdk/deps/heap-allocator/src/allocator.rs': '0f1e4d395e3fe8c36f8304b891bb87799387c099e351e67fb82098a36bb13a7b',
 'osdk/deps/heap-allocator/src/lib.rs': '29f6ba3cb3d516d20882a88c43ec8b0d6f31471be525291c1b168e2d6998e4d8',
 'osdk/deps/heap-allocator/Cargo.toml': '50ff2790cff95e93b91d9ac61e91e89d7d8236a073dd34afd234da0b3d6a8c68',
 'kernel/core/src/lib.rs': '12b2be29a26eb03ab60fff0f133ac0470c13a25a084c14e10a5b824a7536d136',
 'kernel/core/src/syscall/prctl.rs': '05cc746a23aff92f5ff5f834c011c1b3a08d94ba916d92b2cebfcf83ed16b89a'}


def instrument_allocator(text):
    text = once(text, """        let size_class = CommonSizeClass::from_size(SLOT_SIZE).unwrap();
        let mut global_pool = GLOBAL_POOL.lock();
        for _ in 0..OBJ_CACHE_EXPECTED_SIZE / SLOT_SIZE {
            if let Ok(slot) = global_pool.alloc(size_class) {
                self.list.push(slot);
                self.list_size += SLOT_SIZE;
            } else {
                break;
            }
        }

        if let Ok(new_slot) = global_pool.alloc(size_class) {
            Ok(new_slot)
        } else if let Some(popped) = self.list.pop() {
            self.list_size -= SLOT_SIZE;
            Ok(popped)
        } else {
            Err(AllocError)
        }""", """        let size_class = CommonSizeClass::from_size(SLOT_SIZE).unwrap();
        let mut heap_probe = crate::timer_heap_probe::Scope::begin(0, SLOT_SIZE, self.list_size);
        if let Some(probe) = heap_probe.as_mut() {
            probe.before_lock();
        }
        let mut global_pool = GLOBAL_POOL.lock();
        if let Some(probe) = heap_probe.as_mut() {
            probe.acquired();
        }
        let mut attempted = 0;
        let result = (|| {
            for _ in 0..OBJ_CACHE_EXPECTED_SIZE / SLOT_SIZE {
                attempted += 1;
                if let Ok(slot) = global_pool.alloc(size_class) {
                    self.list.push(slot);
                    self.list_size += SLOT_SIZE;
                } else {
                    break;
                }
            }

            attempted += 1;
            if let Ok(new_slot) = global_pool.alloc(size_class) {
                Ok(new_slot)
            } else if let Some(popped) = self.list.pop() {
                self.list_size -= SLOT_SIZE;
                Ok(popped)
            } else {
                Err(AllocError)
            }
        })();
        if let Some(probe) = heap_probe.as_mut() {
            probe.before_release(self.list_size, attempted, result.is_err());
        }
        drop(global_pool);
        if let Some(probe) = heap_probe.as_mut() {
            probe.released();
        }
        if let Some(probe) = heap_probe {
            probe.finish();
        }
        result""")
    return once(text, """        let mut global_pool = GLOBAL_POOL.lock();
        global_pool.dealloc(slot, class)?;
        for _ in 0..(self.list_size - OBJ_CACHE_EXPECTED_SIZE) / SLOT_SIZE {
            let slot = self.list.pop().expect("The cache size should be ample");
            global_pool.dealloc(slot, class)?;
            self.list_size -= SLOT_SIZE;
        }

        Ok(())""", """        let mut heap_probe = crate::timer_heap_probe::Scope::begin(1, SLOT_SIZE, self.list_size);
        if let Some(probe) = heap_probe.as_mut() {
            probe.before_lock();
        }
        let mut global_pool = GLOBAL_POOL.lock();
        if let Some(probe) = heap_probe.as_mut() {
            probe.acquired();
        }
        let mut attempted = 0;
        let result = (|| {
            attempted += 1;
            global_pool.dealloc(slot, class)?;
            for _ in 0..(self.list_size - OBJ_CACHE_EXPECTED_SIZE) / SLOT_SIZE {
                let slot = self.list.pop().expect("The cache size should be ample");
                attempted += 1;
                global_pool.dealloc(slot, class)?;
                self.list_size -= SLOT_SIZE;
            }

            Ok(())
        })();
        if let Some(probe) = heap_probe.as_mut() {
            probe.before_release(self.list_size, attempted, result.is_err());
        }
        drop(global_pool);
        if let Some(probe) = heap_probe.as_mut() {
            probe.released();
        }
        if let Some(probe) = heap_probe {
            probe.finish();
        }
        result""")


def apply(root):
    timer = root/'kernel/core/src/time/core/timer.rs'
    original = timer.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if digest not in HASHES:
        raise ValueError(f'timer source is not an exact frozen variant: {digest}')
    variant = HASHES[digest]
    for path, expected in PRISTINE.items():
        actual = hashlib.sha256((root/path).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f'not a pristine diagnostic input: {path}: {actual}')
    allocator = root/'osdk/deps/heap-allocator/src/allocator.rs'
    allocator_text = instrument_allocator(allocator.read_text())
    allocator_lib = root/'osdk/deps/heap-allocator/src/lib.rs'
    allocator_lib_text = once(allocator_lib.read_text(), 'mod allocator;',
                              'pub mod timer_heap_probe;\n\nmod allocator;')
    allocator_cargo = root/'osdk/deps/heap-allocator/Cargo.toml'
    allocator_cargo_text = once(allocator_cargo.read_text(), 'ostd.workspace = true',
                                'ostd.workspace = true\nspin.workspace = true')
    allocator_module = root/'osdk/deps/heap-allocator/src/timer_heap_probe.rs'
    if allocator_module.exists():
        raise ValueError('heap probe already exists; restore pristine inputs first')
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
    body = once(body, '            let current_time = self.clock.read_time();', """            let detail_start = batch.as_mut().map(|batch| batch.detail_start());
            let current_time = self.clock.read_time();
            if let (Some(batch), Some(start)) = (batch.as_mut(), detail_start) {
                batch.detail_end(0, start);
            }""")
    cancelled_pop = ('                    timeout_list.pop();' if variant == 'heap'
                     else '                    timeout_list.pop_first();')
    body = once(body, cancelled_pop, f"""                    let detail_start = batch.as_mut().map(|batch| batch.detail_start());
{cancelled_pop}
                    if let (Some(batch), Some(start)) = (batch.as_mut(), detail_start) {{
                        batch.detail_end(1, start);
                    }}""")
    selected_pop = ('timeout_list.pop().unwrap()' if variant == 'heap'
                    else 'timeout_list.pop_first().unwrap().1')
    body = once(body, push, f"""                    let detail_start = batch.as_mut().map(|batch| batch.detail_start());
                    let selected = {selected_pop};
                    if let (Some(batch), Some(start)) = (batch.as_mut(), detail_start) {{
                        batch.detail_end(2, start);
                    }}
                    let detail_start = batch.as_mut().map(|batch| batch.detail_start());
                    callbacks.push(selected);
                    if let (Some(batch), Some(start)) = (batch.as_mut(), detail_start) {{
                        batch.detail_end(3, start);
                    }}""")
    body = once(body, '            callbacks\n        };', '''            if let Some(batch) = batch.as_mut() { batch.collected(timeout_list.len()); }
            (callbacks, current_time)
        };''')
    body = once(body, '        for callback in callbacks {', '''        if let Some(batch) = batch.as_mut() { batch.callbacks(); }
        for callback in callbacks {''')
    body = once(body, '            callback.call();', """            let outcome = callback.call(batch.as_mut());
            if let Some(batch) = batch.as_mut() {
                let late = if outcome == 2 {
                    current_time.saturating_sub(callback.expired_time)
                        .as_nanos().min(u128::from(u64::MAX)) as u64
                } else { 0 };
                batch.outcome(outcome, late);
            }""")
    instrumented = text[:start] + body + text[end:]
    instrumented = instrumented.replace('callback.call();', 'callback.call(None);')
    call_start = instrumented.index('    fn call(&self) {')
    call_end = instrumented.index('\n    }', call_start) + len('\n    }')
    call = instrumented[call_start:call_end]
    call = once(call, '    fn call(&self) {', '    fn call(&self, mut batch: Option<&mut crate::batch_probe::Batch>) -> u8 {')
    call = once(call, 'let Some(timer) = self.timer.upgrade() else {\n            return;',
                'let Some(timer) = self.timer.upgrade() else {\n            return 0;')
    call = once(call, '// The callback is cancelled.\n            return;',
                '// The callback is cancelled.\n            return 1;')
    call = once(call, '        let mut timer_guard = timer.lock();', """        let detail_start = batch.as_mut().map(|batch| batch.detail_start());
        let mut timer_guard = timer.lock();
        if let (Some(batch), Some(start)) = (batch.as_mut(), detail_start) {
            batch.detail_end(4, start);
        }""")
    call = once(call, '            timer_guard.set_timeout(Timeout::After(interval));', """            let detail_start = batch.as_mut().map(|batch| batch.detail_start());
            timer_guard.set_timeout(Timeout::After(interval));
            if let (Some(batch), Some(start)) = (batch.as_mut(), detail_start) {
                batch.detail_end(5, start);
            }""")
    call = once(call, '        (timer.registered_callback)(timer_guard);', """        let detail_start = batch.as_mut().map(|batch| batch.detail_start());
        (timer.registered_callback)(timer_guard);
        if let (Some(batch), Some(start)) = (batch.as_mut(), detail_start) {
            batch.detail_end(6, start);
        }
        2""")
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
    allocator_source = Path(__file__).with_name('timer_heap_probe.rs').read_bytes()
    allocator_module.write_bytes(allocator_source)
    allocator.write_text(allocator_text)
    allocator_lib.write_text(allocator_lib_text)
    allocator_cargo.write_text(allocator_cargo_text)
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
