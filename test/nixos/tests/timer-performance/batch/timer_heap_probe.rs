// SPDX-License-Identifier: MPL-2.0

//! Temporary hooks for object-cache refill/drain against the shared heap pool.

use ostd::{arch::read_tsc, cpu::CpuId};
use spin::Once;

/// Fixed hooks installed by the diagnostic kernel at its first START.
pub struct Hooks {
    pub begin: fn() -> Option<u64>,
    pub end: fn(u64, Event),
}

static HOOKS: Once<Hooks> = Once::new();

/// Installs hooks once, without allocating or invoking them during installation.
pub fn install(hooks: Hooks) {
    HOOKS.call_once(|| hooks);
}

/// One refill or drain, with no owned allocation or allocator reference.
#[derive(Clone, Copy)]
pub struct Event {
    pub entry_cpu: u64,
    pub kind: u64,
    pub size_class: u64,
    pub start_tsc: u64,
    pub acquired_tsc: u64,
    pub before_release_tsc: u64,
    pub released_tsc: u64,
    pub cache_before_bytes: u64,
    pub cache_after_bytes: u64,
    pub attempted_ops: u64,
    pub flags: u64,
    pub exit_cpu: u64,
}

/// An admitted scope. Construct it before acquiring the shared pool guard.
pub(crate) struct Scope {
    epoch: u64,
    end: fn(u64, Event),
    event: Event,
    finished: bool,
}

impl Scope {
    pub(crate) fn begin(kind: u64, size_class: usize, cache_before: usize) -> Option<Self> {
        let hooks = HOOKS.get()?;
        let epoch = (hooks.begin)()?;
        let entry_cpu = 0;
        Some(Self {
            epoch,
            end: hooks.end,
            event: Event {
                entry_cpu,
                kind,
                size_class: size_class as u64,
                start_tsc: 0,
                acquired_tsc: 0,
                before_release_tsc: 0,
                released_tsc: 0,
                cache_before_bytes: cache_before as u64,
                cache_after_bytes: cache_before as u64,
                attempted_ops: 0,
                flags: 0,
                exit_cpu: entry_cpu,
            },
            finished: false,
        })
    }

    /// Call through the already constructed optional scope just before locking.
    pub(crate) fn before_lock(&mut self) {
        self.event.entry_cpu = u32::from(CpuId::current_racy()) as u64;
        self.event.start_tsc = read_tsc();
    }

    pub(crate) fn acquired(&mut self) {
        self.event.acquired_tsc = read_tsc();
    }

    pub(crate) fn before_release(&mut self, cache_after: usize, attempted: u64, error: bool) {
        self.event.cache_after_bytes = cache_after as u64;
        self.event.attempted_ops = attempted;
        if error {
            self.event.flags |= 2;
        }
        self.event.before_release_tsc = read_tsc();
    }

    /// Stamp through the existing optional scope before moving it into finish.
    pub(crate) fn released(&mut self) {
        self.event.released_tsc = read_tsc();
        self.event.exit_cpu = u32::from(CpuId::current_racy()) as u64;
    }

    /// Publish only after the pool guard is dropped and released is stamped.
    pub(crate) fn finish(mut self) {
        self.finished = true;
        (self.end)(self.epoch, self.event);
    }
}

impl Drop for Scope {
    fn drop(&mut self) {
        if self.finished {
            return;
        }
        // The pool guard was declared after this scope and drops before us on unwind.
        // Missing boundaries must never produce an apparently complete observation.
        self.event.flags |= 1;
        self.event.released_tsc = read_tsc();
        self.event.exit_cpu = u32::from(CpuId::current_racy()) as u64;
        (self.end)(self.epoch, self.event);
    }
}
