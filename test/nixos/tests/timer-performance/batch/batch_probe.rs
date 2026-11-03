// SPDX-License-Identifier: MPL-2.0

//! Temporary SMP timer batch diagnostics. Stage deltas are elapsed TSC ticks.

use alloc::{vec, vec::Vec};
use core::sync::atomic::{AtomicU64, Ordering::*};

use ostd::{
    arch::{read_tsc, tsc_freq},
    mm::VmIo,
    sync::SpinLock,
};

use crate::prelude::*;

pub(crate) const CONTROL: i32 = 0x41534250;
const MAGIC: u64 = 0x4153544241544348;
const VERSION: u64 = 3;
const CAPACITY: usize = 128;
const EWORDS: usize = 34;
const HWORDS: usize = 32;
const TIMER_WORDS: usize = HWORDS + CAPACITY * EWORDS;
const HEAP_MAGIC: u64 = 0x4153544845415044;
const HEAP_HWORDS: usize = 16;
const HEAP_EWORDS: usize = 16;
const HEAP_CAPACITY: usize = 128;
const BLOB_WORDS: usize = TIMER_WORDS + HEAP_HWORDS + HEAP_CAPACITY * HEAP_EWORDS;
// Calls, queue maximum, total/max elapsed, wait maximum, collection maximum,
// callback maximum, cancelled removals, expired selections, dead at call,
// invoked callbacks, maximum collection-time lateness of invoked callbacks,
// bad TSC batches, migrated batches.
static STATS: [AtomicU64; 14] = [const { AtomicU64::new(0) }; 14];
struct Event([AtomicU64; EWORDS]);
static EVENTS: [Event; CAPACITY] =
    [const { Event([const { AtomicU64::new(0) }; EWORDS]) }; CAPACITY];
struct HeapEvent([AtomicU64; HEAP_EWORDS]);
static HEAP_EVENTS: [HeapEvent; HEAP_CAPACITY] =
    [const { HeapEvent([const { AtomicU64::new(0) }; HEAP_EWORDS]) }; HEAP_CAPACITY];
// Bad/incomplete calls, calls, total/max wait, total/max hold, alloc/free attempts.
static HEAP_STATS: [AtomicU64; 8] = [const { AtomicU64::new(0) }; 8];
static HEAP_NEXT: AtomicU64 = AtomicU64::new(0);
static ENABLED: AtomicU64 = AtomicU64::new(0);
static ACTIVE: AtomicU64 = AtomicU64::new(0);
static NEXT: AtomicU64 = AtomicU64::new(0);
static OWNER: AtomicU64 = AtomicU64::new(0);
static THRESHOLD_NS: AtomicU64 = AtomicU64::new(5_000_000);
static THRESHOLD_CYCLES: AtomicU64 = AtomicU64::new(0);
// Only control syscalls hold this lock. A timer hook must never acquire it.
static CONTROL_LOCK: SpinLock<Control> = SpinLock::new(Control {
    epoch: 0,
    case: 0,
    start: 0,
    freeze: 0,
    quiescent: 0,
});
struct Control {
    epoch: u64,
    case: u64,
    start: u64,
    freeze: u64,
    quiescent: u64,
}

pub(crate) struct Batch {
    data: [u64; EWORDS],
    last_tsc: u64,
}
impl Batch {
    pub(crate) fn begin(manager: u64) -> Option<Self> {
        let epoch = ENABLED.load(SeqCst);
        if epoch == 0 {
            return None;
        }
        ACTIVE.fetch_add(1, SeqCst);
        // The recheck closes admission against freeze. START never resets ACTIVE.
        if ENABLED.load(SeqCst) != epoch {
            ACTIVE.fetch_sub(1, SeqCst);
            return None;
        }
        let mut data = [0; EWORDS];
        data[1] = epoch;
        data[2] = manager;
        data[3] = u32::from(ostd::cpu::CpuId::current_racy()) as u64;
        data[4] = read_tsc();
        Some(Self { last_tsc: data[4], data })
    }
    // Detail indices map to event word pairs 20 + 2 * stage and 21 + 2 * stage.
    // These methods are called only through an admitted batch.
    fn timestamp(&mut self) -> u64 {
        let now = read_tsc();
        if now < self.last_tsc {
            self.data[19] |= 1;
        }
        self.last_tsc = now;
        now
    }
    pub(crate) fn detail_start(&mut self) -> u64 {
        self.timestamp()
    }
    pub(crate) fn detail_end(&mut self, stage: usize, start: u64) {
        let end = self.timestamp();
        if end < start {
            self.data[19] |= 1;
            return;
        }
        let elapsed = end - start;
        let index = 20 + 2 * stage;
        self.data[index] = self.data[index].saturating_add(elapsed);
        self.data[index + 1] = self.data[index + 1].max(elapsed);
    }
    pub(crate) fn set_kind(&mut self, kind: u64) {
        self.data[17] = kind;
    }
    pub(crate) fn before_queue(&mut self) {
        self.data[5] = self.timestamp();
    }
    pub(crate) fn acquired(&mut self, len: usize) {
        self.data[6] = self.timestamp();
        self.data[10] = len as u64;
    }
    pub(crate) fn cancelled(&mut self) {
        self.data[12] += 1;
    }
    pub(crate) fn selected(&mut self) {
        self.data[13] += 1;
    }
    pub(crate) fn outcome(&mut self, outcome: u8, lateness_ns: u64) {
        if outcome == 0 {
            self.data[14] += 1;
        }
        if outcome == 2 {
            self.data[15] += 1;
            self.data[16] = self.data[16].max(lateness_ns);
        }
    }
    pub(crate) fn collected(&mut self, len: usize) {
        self.data[7] = self.timestamp();
        self.data[11] = len as u64;
    }
    pub(crate) fn callbacks(&mut self) {
        self.data[8] = self.timestamp();
    }
}
impl Drop for Batch {
    fn drop(&mut self) {
        self.data[9] = self.timestamp();
        // An empty queue skips callbacks. Equal stage boundaries retain its wait.
        if self.data[7] == 0 {
            self.data[7] = self.data[9];
        }
        if self.data[8] == 0 {
            self.data[8] = self.data[9];
            self.data[19] |= 2;
        }
        self.data[18] = u32::from(ostd::cpu::CpuId::current_racy()) as u64;
        let valid = self.data[19] & 1 == 0
            && self.data[4..10].windows(2).all(|x| x[0] <= x[1]);
        let migrated = self.data[3] != self.data[18];
        STATS[0].fetch_add(1, Relaxed);
        STATS[1].fetch_max(self.data[10], Relaxed);
        for (stat, field) in [(7, 12), (8, 13), (9, 14), (10, 15)] {
            STATS[stat].fetch_add(self.data[field], Relaxed);
        }
        STATS[11].fetch_max(self.data[16], Relaxed);
        if migrated {
            STATS[13].fetch_add(1, Relaxed);
            self.data[19] |= 4;
        }
        if valid {
            let total = self.data[9] - self.data[4];
            STATS[2].fetch_add(total, Relaxed);
            STATS[3].fetch_max(total, Relaxed);
            STATS[4].fetch_max(self.data[6] - self.data[5], Relaxed);
            STATS[5].fetch_max(self.data[7] - self.data[6], Relaxed);
            STATS[6].fetch_max(self.data[9] - self.data[8], Relaxed);
        } else {
            STATS[12].fetch_add(1, Relaxed);
            self.data[19] |= 1;
        }
        let slow = valid && self.data[9] - self.data[4] >= THRESHOLD_CYCLES.load(Relaxed);
        if slow || self.data[16] >= THRESHOLD_NS.load(Relaxed) {
            let index = NEXT.fetch_add(1, Relaxed);
            if index < CAPACITY as u64 {
                let event = &EVENTS[index as usize];
                for i in 1..EWORDS {
                    event.0[i].store(self.data[i], Relaxed);
                }
                event.0[0].store(index + 1, Release);
            }
        }
        // All old-epoch writes finish before a successful freeze can publish a snapshot.
        ACTIVE.fetch_sub(1, SeqCst);
    }
}

// Heap scopes join the same epoch and quiescence protocol as timer batches.
fn heap_begin() -> Option<u64> {
    let epoch = ENABLED.load(SeqCst);
    if epoch == 0 {
        return None;
    }
    ACTIVE.fetch_add(1, SeqCst);
    if ENABLED.load(SeqCst) != epoch {
        ACTIVE.fetch_sub(1, SeqCst);
        return None;
    }
    Some(epoch)
}

fn heap_end(epoch: u64, mut event: osdk_heap_allocator::timer_heap_probe::Event) {
    let times = [event.start_tsc, event.acquired_tsc,
        event.before_release_tsc, event.released_tsc];
    if times.contains(&0) || !times.windows(2).all(|pair| pair[0] <= pair[1])
    {
        event.flags |= 1;
    }
    if event.entry_cpu != event.exit_cpu {
        event.flags |= 4;
    }
    HEAP_STATS[1].fetch_add(1, Relaxed);
    HEAP_STATS[if event.kind == 0 { 6 } else { 7 }]
        .fetch_add(event.attempted_ops, Relaxed);
    if event.flags != 0 {
        HEAP_STATS[0].fetch_add(1, Relaxed);
    } else {
        let wait = event.acquired_tsc - event.start_tsc;
        let hold = event.before_release_tsc - event.acquired_tsc;
        HEAP_STATS[2].fetch_add(wait, Relaxed);
        HEAP_STATS[3].fetch_max(wait, Relaxed);
        HEAP_STATS[4].fetch_add(hold, Relaxed);
        HEAP_STATS[5].fetch_max(hold, Relaxed);
    }
    let slow = event.flags == 0
        && event.released_tsc - event.start_tsc >= THRESHOLD_CYCLES.load(Relaxed);
    if event.flags != 0 || slow {
        let index = HEAP_NEXT.fetch_add(1, Relaxed);
        if index < HEAP_CAPACITY as u64 {
            let data = [0, epoch, event.entry_cpu, event.kind, event.size_class,
                event.start_tsc, event.acquired_tsc, event.before_release_tsc,
                event.released_tsc, event.cache_before_bytes, event.cache_after_bytes,
                event.attempted_ops, event.flags, event.exit_cpu, 0, 0];
            let slot = &HEAP_EVENTS[index as usize];
            for i in 1..HEAP_EWORDS {
                slot.0[i].store(data[i], Relaxed);
            }
            slot.0[0].store(index + 1, Release);
        }
    }
    ACTIVE.fetch_sub(1, SeqCst);
}

pub(crate) fn control(
    command: u64,
    arg3: u64,
    arg4: u64,
    arg5: u64,
    ctx: &Context,
) -> Result<isize> {
    if !ctx.posix_thread.credentials().euid().is_root() {
        return_errno!(Errno::EPERM);
    }
    if arg5 != 0 {
        return_errno!(Errno::EINVAL);
    }
    if command == 0 || command == 3 {
        let bytes = if command == 0 {
            64
        } else {
            (BLOB_WORDS * 8) as u64
        };
        if arg3.checked_add(bytes).is_none() {
            return_errno!(Errno::EFAULT);
        }
    }
    if command == 0 {
        if arg4 < 64 {
            return_errno!(Errno::ENOBUFS);
        }
        let epoch = { CONTROL_LOCK.disable_irq().lock().epoch };
        let info = [
            MAGIC,
            VERSION,
            HWORDS as u64,
            EWORDS as u64,
            CAPACITY as u64,
            tsc_freq(),
            epoch,
            ACTIVE.load(SeqCst),
        ];
        ctx.user_space().write_val(arg3 as usize, &info)?;
        return Ok(64);
    }
    // Allocate before acquiring the control spinlock; never copy to userspace under it.
    let mut raw: Vec<u64> = if command == 3 {
        vec![0; BLOB_WORDS]
    } else {
        Vec::new()
    };
    let result;
    {
        let mut control = CONTROL_LOCK.disable_irq().lock();
        let pid = u64::from(ctx.process.pid());
        if OWNER.load(Relaxed) != 0 && OWNER.load(Relaxed) != pid {
            return_errno!(Errno::EPERM);
        }
        match command {
            1 => {
                if arg3 == 0 || arg4 > 1_000_000_000 {
                    return_errno!(Errno::EINVAL);
                }
                if ENABLED.load(SeqCst) != 0
                    || ACTIVE.load(SeqCst) != 0
                    || (control.epoch != 0 && control.quiescent == 0)
                {
                    return_errno!(Errno::EBUSY);
                }
                if control.epoch == u64::MAX {
                    return_errno!(Errno::EOVERFLOW);
                }
                for stat in &STATS {
                    stat.store(0, Relaxed);
                }
                for event in &EVENTS {
                    event.0[0].store(0, Relaxed);
                }
                for stat in &HEAP_STATS {
                    stat.store(0, Relaxed);
                }
                for event in &HEAP_EVENTS {
                    event.0[0].store(0, Relaxed);
                }
                HEAP_NEXT.store(0, Relaxed);
                let threshold = if arg4 == 0 { 5_000_000 } else { arg4 };
                let cycles =
                    (u128::from(tsc_freq()) * u128::from(threshold)).div_ceil(1_000_000_000) as u64;
                THRESHOLD_NS.store(threshold, Relaxed);
                THRESHOLD_CYCLES.store(cycles, Relaxed);
                NEXT.store(0, Relaxed);
                OWNER.store(pid, Relaxed);
                control.epoch += 1;
                control.case = arg3;
                control.start = read_tsc();
                control.freeze = 0;
                control.quiescent = 0;
                if control.epoch == 1 {
                    osdk_heap_allocator::timer_heap_probe::install(
                        osdk_heap_allocator::timer_heap_probe::Hooks {
                            begin: heap_begin,
                            end: heap_end,
                        },
                    );
                }
                ENABLED.store(control.epoch, SeqCst);
                result = 0;
            }
            2 => {
                if OWNER.load(Relaxed) != pid || arg3 != control.case || arg4 != 0 {
                    return_errno!(Errno::EINVAL);
                }
                if ENABLED.swap(0, SeqCst) != 0 {
                    control.freeze = read_tsc();
                }
                if ACTIVE.load(SeqCst) != 0 {
                    return_errno!(Errno::EBUSY);
                }
                if control.quiescent == 0 {
                    control.quiescent = read_tsc();
                }
                result = (BLOB_WORDS * 8) as isize;
            }
            3 => {
                if OWNER.load(Relaxed) != pid {
                    return_errno!(Errno::EPERM);
                }
                let active = ACTIVE.load(SeqCst);
                if ENABLED.load(SeqCst) != 0 || active != 0 || control.quiescent == 0 {
                    return_errno!(Errno::EBUSY);
                }
                if arg4 < (BLOB_WORDS * 8) as u64 {
                    return_errno!(Errno::ENOBUFS);
                }
                let total = NEXT.load(Acquire);
                raw[..18].copy_from_slice(&[
                    MAGIC,
                    VERSION,
                    HWORDS as u64,
                    EWORDS as u64,
                    CAPACITY as u64,
                    tsc_freq(),
                    control.epoch,
                    control.case,
                    OWNER.load(Relaxed),
                    control.start,
                    control.freeze,
                    control.quiescent,
                    total,
                    total.min(CAPACITY as u64),
                    total.saturating_sub(CAPACITY as u64),
                    active,
                    THRESHOLD_NS.load(Relaxed),
                    0,
                ]);
                for i in 0..14 {
                    raw[18 + i] = STATS[i].load(Relaxed);
                }
                for (index, event) in EVENTS
                    .iter()
                    .take(total.min(CAPACITY as u64) as usize)
                    .enumerate()
                {
                    let commit = event.0[0].load(Acquire);
                    if commit != index as u64 + 1 {
                        return_errno!(Errno::EIO);
                    }
                    for i in 0..EWORDS {
                        raw[HWORDS + index * EWORDS + i] = event.0[i].load(Relaxed);
                    }
                }
                let heap_total = HEAP_NEXT.load(Acquire);
                raw[TIMER_WORDS..TIMER_WORDS + 8].copy_from_slice(&[
                    HEAP_MAGIC, VERSION, HEAP_HWORDS as u64, HEAP_EWORDS as u64,
                    HEAP_CAPACITY as u64, heap_total,
                    heap_total.min(HEAP_CAPACITY as u64),
                    heap_total.saturating_sub(HEAP_CAPACITY as u64),
                ]);
                for i in 0..8 {
                    raw[TIMER_WORDS + 8 + i] = HEAP_STATS[i].load(Relaxed);
                }
                for (index, event) in HEAP_EVENTS.iter()
                    .take(heap_total.min(HEAP_CAPACITY as u64) as usize).enumerate()
                {
                    if event.0[0].load(Acquire) != index as u64 + 1 {
                        return_errno!(Errno::EIO);
                    }
                    for i in 0..HEAP_EWORDS {
                        raw[TIMER_WORDS + HEAP_HWORDS + index * HEAP_EWORDS + i] =
                            event.0[i].load(Relaxed);
                    }
                }
                result = (BLOB_WORDS * 8) as isize;
            }
            _ => return_errno!(Errno::EINVAL),
        }
    }
    if command == 3 {
        ctx.user_space().write_slice(arg3 as usize, &raw)?;
    }
    Ok(result)
}
