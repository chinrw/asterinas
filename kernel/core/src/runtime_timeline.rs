// SPDX-License-Identifier: MPL-2.0

//! Temporary single-CPU diagnostic recorder. It does not change scheduling decisions.

use core::{
    sync::atomic::{AtomicU64, Ordering::*},
    time::Duration,
};

use ostd::{arch::read_tsc, mm::VmIo};

use crate::{prelude::*, process::posix_thread::AsPosixThread, thread::AsThread};

pub(crate) const CONTROL: i32 = 0x41535454;
const NSLOTS: usize = 256;
const WORDS: usize = 40;
const CAPACITY: usize = 65536;
const EWORDS: usize = 12;
const MAGIC: u64 = 0x41535454494d4531;
const TID: usize = 0;
const NS: usize = 1;
const MIN_NS: usize = 2;
const MAX_NS: usize = 3;
const SEQ: usize = 4;
const ACTIVE: usize = 5;
const ENTRY: usize = 6;
const WAITING: usize = 7;
const BLOCKED: usize = 8;
const BLOCK_AT: usize = 9;
const WAKE_AT: usize = 10;
const QUEUE_AT: usize = 11;
const RUN_AT: usize = 12;
const DUE_NS: usize = 13;
const WAKE_NS: usize = 14;
const CALLBACK_AT: usize = 15;
const READY_AT: usize = 16;
const READY_KIND: usize = 17;
const CALLS: usize = 18;
const ENDED: usize = 19;
const DID_BLOCK: usize = 20;
const NO_BLOCK: usize = 21;
const SWITCHES: usize = 22;
const PREEMPTS: usize = 23;
const TIMER_WAKES: usize = 24;
const OTHER_WAKES: usize = 25;
const RUNS: usize = 26;
const TOTAL_CYCLES: usize = 27;
const MAX_CYCLES: usize = 28;
const MAX_LATE_NS: usize = 29;
const MAX_WAKE_RUN: usize = 30;
const EINTR_COUNT: usize = 32;
const BAD_TIME: usize = 33;
const SELECT_AT: usize = 34;
const WAIT_ATTEMPTS: usize = 35;
const TOTAL_WAKE_RUN: usize = 36;
const QUEUED_WAKES: usize = 37;
const BLOCK_EVENTS: usize = 38;
const MAX_SELECT_RUN: usize = 39;
struct Slot([AtomicU64; WORDS]);
impl Slot {
    const fn new() -> Self {
        Self([const { AtomicU64::new(0) }; WORDS])
    }
}
struct Event([AtomicU64; EWORDS]);
impl Event {
    const fn new() -> Self {
        Self([const { AtomicU64::new(0) }; EWORDS])
    }
}
static SLOTS: [Slot; NSLOTS] = [const { Slot::new() }; NSLOTS];
static EVENTS: [Event; CAPACITY] = [const { Event::new() }; CAPACITY];
static WATCH: AtomicU64 = AtomicU64::new(0);
static OWNER: AtomicU64 = AtomicU64::new(0);
static TARGET: AtomicU64 = AtomicU64::new(0);
static MODE: AtomicU64 = AtomicU64::new(0);
static START: AtomicU64 = AtomicU64::new(0);
static STOP: AtomicU64 = AtomicU64::new(0);
static NEXT: AtomicU64 = AtomicU64::new(0);
static COLLISIONS: AtomicU64 = AtomicU64::new(0);
static TIMER_SCOPE: AtomicU64 = AtomicU64::new(0);
static TIMER_DUE: AtomicU64 = AtomicU64::new(0);
static TIMER_AT: AtomicU64 = AtomicU64::new(0);

static TIMER_BUDGET: AtomicU64 = AtomicU64::new(64);
static DAEMON_TASK: AtomicU64 = AtomicU64::new(0);
static FAIR_PICKS: AtomicU64 = AtomicU64::new(0);
static GLOBAL_SLOT: Slot = Slot::new();
// Gap, inserts, heap maximum, insert maximum, collections, collection maximum,
// collection total, callback batch maximum, callback batch total, drained,
// cancelled, maximum batch, dead callbacks, live callbacks.
static TIMER_STATS: [AtomicU64; 14] = [const { AtomicU64::new(0) }; 14];
static LAST_SOFTIRQ_END: AtomicU64 = AtomicU64::new(0);
static SOFTIRQ_CALLS: AtomicU64 = AtomicU64::new(0);
static SOFTIRQ_CYCLES: AtomicU64 = AtomicU64::new(0);
static SOFTIRQ_MAX: AtomicU64 = AtomicU64::new(0);

pub(crate) fn softirq_begin() -> Option<u64> {
    if !timing_enabled() {
        return None;
    }
    let now = read_tsc();
    let last = LAST_SOFTIRQ_END.load(Relaxed);
    if last != 0 {
        TIMER_STATS[0].fetch_max(now.saturating_sub(last), Relaxed);
    }
    let task = ostd::task::Task::current().unwrap();
    let pointer = &*task as *const ostd::task::Task as u64;
    let tid = task
        .as_posix_thread()
        .map_or(0, |thread| u64::from(thread.tid()));
    let in_task = ostd::irq::InterruptLevel::current().is_task_context();
    if in_task && tid == 0 && task.as_thread().is_some() {
        DAEMON_TASK.store(pointer, Relaxed);
    }
    emit(&GLOBAL_SLOT, 12, u64::from(in_task), pointer, tid);
    Some(now)
}
pub(crate) fn softirq_end(start: Option<u64>) {
    let Some(start) = start else {
        return;
    };
    if WATCH.load(Relaxed) == 0 {
        return;
    }
    let elapsed = read_tsc().saturating_sub(start);
    SOFTIRQ_CALLS.fetch_add(1, Relaxed);
    SOFTIRQ_CYCLES.fetch_add(elapsed, Relaxed);
    SOFTIRQ_MAX.fetch_max(elapsed, Relaxed);
    LAST_SOFTIRQ_END.store(read_tsc(), Relaxed);
    emit(&GLOBAL_SLOT, 13, elapsed, 0, 0);
}

pub(crate) fn timer_budget() -> usize {
    match TIMER_BUDGET.load(Relaxed) {
        0 => usize::MAX,
        value => value as usize,
    }
}

pub(crate) fn daemon_event(task: u64, kind: u64) {
    if timing_enabled() && DAEMON_TASK.load(Relaxed) == task {
        emit(&GLOBAL_SLOT, kind, task, 0, 0);
    }
}
pub(crate) fn fair_queued(task: u64, wake: bool, vruntime: u64, minimum: u64) {
    if timing_enabled() && DAEMON_TASK.load(Relaxed) == task {
        emit(
            &GLOBAL_SLOT,
            if wake { 21 } else { 23 },
            task,
            vruntime,
            minimum,
        );
    }
}
pub(crate) fn fair_picked(task: u64, vruntime: u64, minimum: u64) {
    if !timing_enabled() {
        return;
    }
    let sample = FAIR_PICKS.fetch_add(1, Relaxed);
    if DAEMON_TASK.load(Relaxed) == task || sample % 128 == 0 {
        emit(&GLOBAL_SLOT, 22, task, vruntime, minimum);
    }
}

pub(crate) fn timer_operation_start() -> Option<u64> {
    timing_enabled().then(read_tsc)
}
pub(crate) fn timer_inserted(start: Option<u64>, len: usize, manager: u64) {
    let Some(start) = start else {
        return;
    };
    let elapsed = read_tsc().saturating_sub(start);
    TIMER_STATS[1].fetch_add(1, Relaxed);
    TIMER_STATS[2].fetch_max(len as u64, Relaxed);
    TIMER_STATS[3].fetch_max(elapsed, Relaxed);
    if elapsed > ostd::arch::tsc_freq() / 1000 {
        emit(&GLOBAL_SLOT, 16, elapsed, len as u64, manager);
    }
}
pub(crate) fn timer_collected(start: Option<u64>, drained: usize, callbacks: usize, manager: u64) {
    let Some(start) = start else {
        return;
    };
    let elapsed = read_tsc().saturating_sub(start);
    TIMER_STATS[4].fetch_add(1, Relaxed);
    TIMER_STATS[5].fetch_max(elapsed, Relaxed);
    TIMER_STATS[6].fetch_add(elapsed, Relaxed);
    TIMER_STATS[9].fetch_add(drained as u64, Relaxed);
    TIMER_STATS[10].fetch_add((drained - callbacks) as u64, Relaxed);
    TIMER_STATS[11].fetch_max(callbacks as u64, Relaxed);
    emit(&GLOBAL_SLOT, 14, elapsed, drained as u64, manager);
}
pub(crate) fn timer_batch_done(start: Option<u64>, manager: u64) {
    let Some(start) = start else {
        return;
    };
    let elapsed = read_tsc().saturating_sub(start);
    TIMER_STATS[7].fetch_max(elapsed, Relaxed);
    TIMER_STATS[8].fetch_add(elapsed, Relaxed);
    emit(&GLOBAL_SLOT, 15, elapsed, 0, manager);
}
pub(crate) fn timer_callback(live: bool) {
    if timing_enabled() {
        TIMER_STATS[if live { 13 } else { 12 }].fetch_add(1, Relaxed);
    }
}

fn slot(tid: u32) -> Option<&'static Slot> {
    if WATCH.load(Relaxed) == 0 {
        return None;
    }
    let slot = &SLOTS[tid as usize % NSLOTS];
    (slot.0[TID].load(Relaxed) == u64::from(tid)).then_some(slot)
}
fn emit(slot: &Slot, kind: u64, a: u64, b: u64, c: u64) {
    if MODE.load(Relaxed) != 2 || WATCH.load(Relaxed) == 0 {
        return;
    }
    let seq = slot.0[SEQ].load(Relaxed);
    if slot.0[NS].load(Relaxed) == 1000 && seq % 1024 != 1 && slot.0[BLOCKED].load(Relaxed) == 0 {
        return;
    }
    let index = NEXT.fetch_add(1, Relaxed) as usize;
    if index >= CAPACITY {
        return;
    }
    let data = [
        index as u64 + 1,
        read_tsc(),
        kind,
        slot.0[TID].load(Relaxed),
        seq,
        slot.0[NS].load(Relaxed),
        a,
        b,
        c,
        slot.0[ACTIVE].load(Relaxed),
        0,
        0,
    ];
    for (field, value) in EVENTS[index].0.iter().skip(1).zip(data.into_iter().skip(1)) {
        field.store(value, Relaxed);
    }
    EVENTS[index].0[0].store(index as u64 + 1, Release);
}

pub(crate) fn begin(pid: u32, tid: u32, ns: u64) -> Option<usize> {
    if WATCH.load(Relaxed) != u64::from(pid) {
        return None;
    }
    let index = tid as usize % NSLOTS;
    let slot = &SLOTS[index];
    let old = slot.0[TID].load(Relaxed);
    if old != 0 && old != u64::from(tid) {
        COLLISIONS.fetch_add(1, Relaxed);
        return None;
    }
    slot.0[TID].store(u64::from(tid), Relaxed);
    slot.0[ACTIVE].store(0, Relaxed);
    slot.0[NS].store(ns, Relaxed);
    if slot.0[MIN_NS].load(Relaxed) == 0 {
        slot.0[MIN_NS].store(ns, Relaxed);
    } else {
        slot.0[MIN_NS].fetch_min(ns, Relaxed);
    }
    slot.0[MAX_NS].fetch_max(ns, Relaxed);
    let seq = slot.0[SEQ].fetch_add(1, Relaxed) + 1;
    for field in [
        WAITING,
        BLOCKED,
        BLOCK_AT,
        WAKE_AT,
        QUEUE_AT,
        RUN_AT,
        DUE_NS,
        WAKE_NS,
        CALLBACK_AT,
    ] {
        slot.0[field].store(0, Relaxed);
    }
    slot.0[ENTRY].store(read_tsc(), Relaxed);
    slot.0[CALLS].fetch_add(1, Relaxed);
    slot.0[ACTIVE].store(seq, Release);
    emit(slot, 1, 0, 0, 0);
    Some(index)
}
pub(crate) fn end(token: Option<usize>, interrupted: bool) {
    let Some(index) = token else {
        return;
    };
    if WATCH.load(Acquire) == 0 {
        return;
    }
    let _guard = ostd::task::disable_preempt();
    let slot = &SLOTS[index];
    let now = read_tsc();
    let entry = slot.0[ENTRY].load(Relaxed);
    if now < entry {
        slot.0[BAD_TIME].fetch_add(1, Relaxed);
    } else {
        slot.0[TOTAL_CYCLES].fetch_add(now - entry, Relaxed);
        slot.0[MAX_CYCLES].fetch_max(now - entry, Relaxed);
    }
    slot.0[ENDED].fetch_add(1, Relaxed);
    if slot.0[BLOCKED].load(Relaxed) == 0 {
        slot.0[NO_BLOCK].fetch_add(1, Relaxed);
    }
    if interrupted {
        slot.0[EINTR_COUNT].fetch_add(1, Relaxed);
    }
    emit(
        slot,
        9,
        now,
        u64::from(interrupted),
        slot.0[BLOCKED].load(Relaxed),
    );
    slot.0[ACTIVE].store(0, Release);
}
pub(crate) fn wait(tid: u32) {
    let Some(slot) = slot(tid) else {
        return;
    };
    slot.0[WAITING].store(1, Relaxed);
    slot.0[WAIT_ATTEMPTS].fetch_add(1, Relaxed);
    emit(slot, 2, 0, 0, 0);
}
pub(crate) fn switch_out(tid: u32) {
    let Some(slot) = slot(tid) else {
        return;
    };
    slot.0[SWITCHES].fetch_add(1, Relaxed);
    if slot.0[WAITING].swap(0, Relaxed) != 0 {
        if slot.0[ACTIVE].load(Acquire) != 0 && slot.0[BLOCKED].swap(1, Relaxed) == 0 {
            slot.0[DID_BLOCK].fetch_add(1, Relaxed);
            slot.0[BLOCK_AT].store(read_tsc(), Relaxed);
        }
        slot.0[BLOCK_EVENTS].fetch_add(1, Relaxed);
        emit(slot, 3, 0, 0, 0);
    } else {
        slot.0[PREEMPTS].fetch_add(1, Relaxed);
        emit(slot, 11, 0, 0, 0);
    }
}
pub(crate) fn selected(tid: u32) {
    let Some(slot) = slot(tid) else {
        return;
    };
    slot.0[SELECT_AT].store(read_tsc(), Relaxed);
    emit(slot, 7, 0, 0, 0);
}
pub(crate) fn running(tid: u32) {
    let Some(slot) = slot(tid) else {
        return;
    };
    let now = read_tsc();
    slot.0[RUNS].fetch_add(1, Relaxed);
    let ready = slot.0[READY_AT].swap(0, Relaxed);
    if ready != 0 && now >= ready {
        slot.0[MAX_WAKE_RUN].fetch_max(now - ready, Relaxed);
        slot.0[TOTAL_WAKE_RUN].fetch_add(now - ready, Relaxed);
    }
    let selected = slot.0[SELECT_AT].load(Relaxed);
    if selected != 0 && now >= selected {
        slot.0[MAX_SELECT_RUN].fetch_max(now - selected, Relaxed);
    }
    if slot.0[ACTIVE].load(Acquire) != 0
        && slot.0[BLOCKED].load(Relaxed) != 0
        && slot.0[RUN_AT].load(Relaxed) == 0
    {
        slot.0[RUN_AT].store(now, Relaxed);
    }
    emit(slot, 8, ready, selected, now);
}
pub(crate) fn wake_attempt(tid: u32) {
    let Some(slot) = slot(tid) else {
        return;
    };
    let now = read_tsc();
    if TIMER_SCOPE.load(Relaxed) != 0 {
        slot.0[TIMER_WAKES].fetch_add(1, Relaxed);
        let due = TIMER_DUE.load(Relaxed);
        use crate::time::clocks::MonotonicClock;
        let clock = MonotonicClock::get().read_time().as_nanos() as u64;
        if slot.0[ACTIVE].load(Acquire) != 0 && slot.0[WAKE_AT].load(Relaxed) == 0 {
            slot.0[WAKE_AT].store(now, Relaxed);
            slot.0[DUE_NS].store(due, Relaxed);
            slot.0[WAKE_NS].store(clock, Relaxed);
            slot.0[CALLBACK_AT].store(TIMER_AT.load(Relaxed), Relaxed);
        }
        if clock >= due {
            slot.0[MAX_LATE_NS].fetch_max(clock - due, Relaxed);
        }
        emit(slot, 4, due, clock, TIMER_AT.load(Relaxed));
    } else {
        slot.0[OTHER_WAKES].fetch_add(1, Relaxed);
        emit(slot, 5, 0, 0, 0);
    }
}
pub(crate) fn enqueued(tid: u32) {
    let Some(slot) = slot(tid) else {
        return;
    };
    let now = read_tsc();
    slot.0[READY_AT].store(now, Relaxed);
    slot.0[READY_KIND].store(1, Relaxed);
    slot.0[QUEUED_WAKES].fetch_add(1, Relaxed);
    if slot.0[ACTIVE].load(Acquire) != 0 && slot.0[QUEUE_AT].load(Relaxed) == 0 {
        slot.0[QUEUE_AT].store(now, Relaxed);
    }
    emit(slot, 6, 0, 0, 0);
}
pub(crate) fn exited(tid: u32) {
    if let Some(slot) = slot(tid) {
        emit(slot, 10, 0, 0, 0);
    }
}

pub(crate) fn timing_enabled() -> bool {
    MODE.load(Relaxed) == 2 && WATCH.load(Relaxed) != 0
}

pub(crate) struct TimerScope(Option<(u64, u64, u64)>);
impl TimerScope {
    pub(crate) fn new(monotonic: bool, deadline: Duration) -> Self {
        if MODE.load(Relaxed) != 2 || WATCH.load(Relaxed) == 0 || !monotonic {
            return Self(None);
        }
        let old = (
            TIMER_SCOPE.load(Relaxed),
            TIMER_DUE.load(Relaxed),
            TIMER_AT.load(Relaxed),
        );
        TIMER_DUE.store(deadline.as_nanos() as u64, Relaxed);
        TIMER_AT.store(read_tsc(), Relaxed);
        TIMER_SCOPE.store(1, Relaxed);
        Self(Some(old))
    }
}
impl Drop for TimerScope {
    fn drop(&mut self) {
        if let Some((scope, due, at)) = self.0 {
            TIMER_SCOPE.store(scope, Relaxed);
            TIMER_DUE.store(due, Relaxed);
            TIMER_AT.store(at, Relaxed);
        }
    }
}

pub(crate) fn control(
    command: u64,
    arg3: u64,
    arg4: u64,
    _arg5: u64,
    ctx: &Context,
) -> Result<isize> {
    if !ctx.posix_thread.credentials().euid().is_root() {
        return_errno!(Errno::EPERM);
    }
    match command {
        0 => return Ok(1),
        4 => {
            if WATCH.load(Acquire) != 0 {
                return_errno!(Errno::EBUSY);
            }
            if arg3 != 0 && arg3 != 64 {
                return_errno!(Errno::EINVAL);
            }
            TIMER_BUDGET.store(arg3, Release);
        }
        1 => {
            if ostd::cpu::num_cpus() != 1 || arg3 == 0 || arg3 > u32::MAX as u64 || arg4 > 2 {
                return_errno!(Errno::EINVAL);
            }
            if WATCH.load(Acquire) != 0 {
                return_errno!(Errno::EBUSY);
            }
            OWNER.store(u64::from(ctx.process.pid()), Relaxed);
            TARGET.store(arg3, Relaxed);
            MODE.store(arg4, Relaxed);
            for slot in &SLOTS {
                for word in &slot.0 {
                    word.store(0, Relaxed);
                }
            }
            for event in &EVENTS {
                event.0[0].store(0, Relaxed);
            }
            DAEMON_TASK.store(0, Relaxed);
            FAIR_PICKS.store(0, Relaxed);
            for field in &TIMER_STATS {
                field.store(0, Relaxed);
            }
            LAST_SOFTIRQ_END.store(0, Relaxed);
            SOFTIRQ_CALLS.store(0, Relaxed);
            SOFTIRQ_CYCLES.store(0, Relaxed);
            SOFTIRQ_MAX.store(0, Relaxed);
            NEXT.store(0, Relaxed);
            COLLISIONS.store(0, Relaxed);
            STOP.store(0, Relaxed);
            START.store(read_tsc(), Relaxed);
            WATCH.store(if arg4 == 0 { 0 } else { arg3 }, Release);
        }
        2 | 3 => {
            if OWNER.load(Relaxed) != u64::from(ctx.process.pid()) {
                return_errno!(Errno::EPERM);
            }
            if command == 2 {
                WATCH.store(0, Release);
                STOP.store(read_tsc(), Relaxed);
            }
            if command == 3 && WATCH.load(Acquire) != 0 {
                return_errno!(Errno::EBUSY);
            }
            let total = NEXT.load(Acquire);
            let stored = total.min(CAPACITY as u64) as usize;
            let size = 48 * 8 + NSLOTS * WORDS * 8 + stored * EWORDS * 8;
            if command == 2 {
                return Ok(size as _);
            }
            if arg4 < size as u64 {
                return_errno!(Errno::ENOBUFS);
            }
            let mut header: [u64; 48] = [
                MAGIC,
                3,
                NSLOTS as u64,
                CAPACITY as u64,
                EWORDS as u64,
                WORDS as u64,
                ostd::arch::tsc_freq(),
                TARGET.load(Relaxed),
                START.load(Relaxed),
                STOP.load(Relaxed),
                total,
                stored as u64,
                total - stored as u64,
                COLLISIONS.load(Relaxed),
                MODE.load(Relaxed),
                0,
                SOFTIRQ_CALLS.load(Relaxed),
                SOFTIRQ_CYCLES.load(Relaxed),
                SOFTIRQ_MAX.load(Relaxed),
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
            ];
            for (i, field) in TIMER_STATS.iter().enumerate() {
                header[24 + i] = field.load(Relaxed);
            }
            header[38] = DAEMON_TASK.load(Relaxed);
            header[39] = FAIR_PICKS.load(Relaxed);
            header[40] = TIMER_BUDGET.load(Relaxed);
            let space = ctx.user_space();
            space.write_val(arg3 as usize, &header)?;
            let mut address = arg3 as usize + size_of_val(&header);
            for slot in &SLOTS {
                let raw: [u64; WORDS] = core::array::from_fn(|i| slot.0[i].load(Acquire));
                space.write_val(address, &raw)?;
                address += size_of::<[u64; WORDS]>();
            }
            for event in EVENTS.iter().take(stored) {
                let raw: [u64; EWORDS] = core::array::from_fn(|i| event.0[i].load(Acquire));
                space.write_val(address, &raw)?;
                address += size_of::<[u64; EWORDS]>();
            }
            return Ok(size as _);
        }
        _ => return_errno!(Errno::EINVAL),
    }
    Ok(0)
}
