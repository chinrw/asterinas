// SPDX-License-Identifier: MPL-2.0

//! Temporary timer census with per-CPU counters and frozen control snapshots.

use alloc::{vec, vec::Vec};
use core::{
    cell::Cell,
    sync::atomic::{AtomicU64, Ordering::*},
};

use ostd::{
    arch::{read_tsc, tsc_freq},
    cpu::PinCurrentCpu,
    mm::VmIo,
    task::{DisabledPreemptGuard, disable_preempt},
};

use crate::prelude::*;

pub(crate) const CONTROL: i32 = 0x41534351;
const MAGIC: u64 = 0x41535443454e5355;
const VERSION: u64 = 1;
const HWORDS: usize = 16;
const CPUS: usize = 64;
const KINDS: usize = 5;
const FIELDS: usize = 128;
const WORDS: usize = HWORDS + CPUS * KINDS * FIELDS;
const BYTES: usize = WORDS * 8;

#[derive(Clone, Copy)]
pub(crate) enum Kind {
    Realtime = 0,
    Monotonic = 1,
    Boottime = 2,
    Jiffies = 3,
    CpuTime = 4,
}

#[derive(Clone, Copy)]
pub(crate) enum Reason {
    Explicit = 9,
    Rearm = 10,
    OwnerDrop = 11,
    Periodic = 27,
}

#[derive(Clone, Copy)]
pub(crate) enum Timing {
    Insert = 48,
    RetireHit = 58,
    RetireMiss = 68,
    Dispatch = 78,
    Collection = 88,
    Callback = 98,
    QueueWait = 108,
    QueueHold = 118,
}

#[repr(align(64))]
struct Row([AtomicU64; FIELDS]);
#[repr(align(64))]
struct Active(AtomicU64);
struct Cpu {
    active: Active,
    rows: [Row; KINDS],
}
static COUNTERS: [Cpu; CPUS] = [const {
    Cpu {
        active: Active(AtomicU64::new(0)),
        rows: [const { Row([const { AtomicU64::new(0) }; FIELDS]) }; KINDS],
    }
}; CPUS];
// The low two bits carry the mode. A zero gate rejects all timer hooks.
static ENABLED: AtomicU64 = AtomicU64::new(0);
static CONTROL_LOCK: SpinLock<Control> = SpinLock::new(Control {
    epoch: 0,
    owner: 0,
    case: 0,
    mode: 0,
    running: false,
    start: 0,
    freeze: 0,
    quiescent: 0,
});
struct Control {
    epoch: u64,
    owner: u64,
    case: u64,
    mode: u64,
    running: bool,
    start: u64,
    freeze: u64,
    quiescent: u64,
}

pub(crate) struct Scope {
    cpu: Option<&'static Cpu>,
    row: Option<&'static Row>,
    timed: bool,
    last_tsc: Cell<Option<u64>>,
    backwards: Cell<u64>,
    _preempt: Option<DisabledPreemptGuard>,
}

impl Scope {
    pub(crate) fn new(kind: Kind) -> Self {
        let mut scope = Self {
            cpu: None,
            row: None,
            timed: false,
            last_tsc: Cell::new(None),
            backwards: Cell::new(0),
            _preempt: None,
        };
        let gate = ENABLED.load(SeqCst);
        if gate == 0 {
            return scope;
        }
        let preempt = disable_preempt();
        let index = u32::from(preempt.current_cpu()) as usize;
        let Some(cpu) = COUNTERS.get(index) else {
            return scope;
        };
        cpu.active.0.fetch_add(1, SeqCst);
        // A delayed admission may cross STOP and START. It must not touch the new rows.
        if ENABLED.load(SeqCst) != gate {
            cpu.active.0.fetch_sub(1, SeqCst);
            return scope;
        }
        scope.cpu = Some(cpu);
        scope.row = Some(&cpu.rows[kind as usize]);
        scope.timed = gate & 3 == 2;
        scope._preempt = Some(preempt);
        scope
    }

    pub(crate) fn add(&self, field: usize, value: u64) {
        if let Some(row) = self.row {
            row.0[field].fetch_add(value, Relaxed);
        }
    }

    fn max(&self, field: usize, value: u64) {
        if let Some(row) = self.row {
            row.0[field].fetch_max(value, Relaxed);
        }
    }

    pub(crate) fn reason(&self, reason: Reason) {
        if matches!(reason, Reason::Periodic) {
            self.add(10, 1);
        }
        self.add(reason as usize, 1);
    }

    pub(crate) fn occupancy(&self, len: usize) {
        let len = len as u64;
        self.add(18, 1);
        self.add(19, len);
        self.max(20, len);
        self.add(32 + size_bucket(len), 1);
    }

    pub(crate) fn batch(&self, len: usize) {
        let len = len as u64;
        self.add(21, 1);
        self.add(22, len);
        self.max(23, len);
        self.add(40 + size_bucket(len), 1);
    }

    pub(crate) fn timestamp(&self) -> u64 {
        if !self.timed {
            return 0;
        }
        let now = read_tsc();
        if self
            .last_tsc
            .replace(Some(now))
            .is_some_and(|last| now < last)
        {
            self.backwards.set(self.backwards.get() + 1);
        }
        now
    }

    pub(crate) fn elapsed(&self, timing: Timing, start: u64) {
        if !self.timed {
            return;
        }
        let end = self.timestamp();
        self.elapsed_between(timing, start, end);
    }

    pub(crate) fn elapsed_between(&self, timing: Timing, start: u64, end: u64) {
        if !self.timed {
            return;
        }
        let Some(ticks) = end.checked_sub(start) else {
            return;
        };
        let index = timing as usize;
        self.add(index, ticks);
        self.max(index + 1, ticks);
        let bucket = match ticks {
            0..256 => 0,
            256..1024 => 1,
            1024..4096 => 2,
            4096..16384 => 3,
            16384..65536 => 4,
            65536..262144 => 5,
            262144..1048576 => 6,
            _ => 7,
        };
        self.add(index + 2 + bucket, 1);
    }
}

impl Drop for Scope {
    fn drop(&mut self) {
        if let Some(cpu) = self.cpu {
            if self.backwards.get() != 0 {
                self.add(26, self.backwards.get());
            }
            // STOP can publish a frozen snapshot only after all admitted writes finish.
            cpu.active.0.fetch_sub(1, SeqCst);
        }
    }
}

fn size_bucket(size: u64) -> usize {
    match size {
        0 => 0,
        1 => 1,
        2..=3 => 2,
        4..=15 => 3,
        16..=63 => 4,
        64..=255 => 5,
        256..=1023 => 6,
        _ => 7,
    }
}

fn active() -> u64 {
    COUNTERS.iter().map(|cpu| cpu.active.0.load(SeqCst)).sum()
}

pub(crate) fn control(
    command: u64,
    arg3: u64,
    arg4: u64,
    arg5: u64,
    ctx: &Context,
) -> Result<isize> {
    if command > 3 || arg5 != 0 {
        return_errno!(Errno::EINVAL);
    }
    if command == 0 || command == 3 {
        let bytes = if command == 0 { 64 } else { BYTES };
        if arg4 < bytes as u64 {
            return_errno!(Errno::ENOBUFS);
        }
        if usize::try_from(arg3).is_err() || arg3.checked_add(bytes as u64).is_none() {
            return_errno!(Errno::EFAULT);
        }
    }
    if command == 0 {
        let info = [
            MAGIC,
            VERSION,
            HWORDS as u64,
            CPUS as u64,
            KINDS as u64,
            FIELDS as u64,
            tsc_freq(),
            BYTES as u64,
        ];
        ctx.user_space().write_val(arg3 as usize, &info)?;
        return Ok(64);
    }
    // Allocation and user-memory access must stay outside the control spinlock.
    let mut raw: Vec<u64> = if command == 3 {
        vec![0; WORDS]
    } else {
        Vec::new()
    };
    let result;
    {
        let mut control = CONTROL_LOCK.disable_irq().lock();
        let pid = u64::from(ctx.process.pid());
        match command {
            1 => {
                if arg3 == 0 || arg4 > 2 {
                    return_errno!(Errno::EINVAL);
                }
                if control.running
                    || active() != 0
                    || (control.epoch != 0 && control.quiescent == 0)
                {
                    return_errno!(Errno::EBUSY);
                }
                if control.epoch == (isize::MAX as u64) >> 2 {
                    return_errno!(Errno::EOVERFLOW);
                }
                for cpu in &COUNTERS {
                    for row in &cpu.rows {
                        for field in &row.0 {
                            field.store(0, Relaxed);
                        }
                    }
                }
                // ACTIVE is never reset: rejected admissions still own their decrements.
                control.epoch += 1;
                control.owner = pid;
                control.case = arg3;
                control.mode = arg4;
                control.running = true;
                control.start = read_tsc();
                control.freeze = 0;
                control.quiescent = 0;
                if arg4 != 0 {
                    ENABLED.store((control.epoch << 2) | arg4, SeqCst);
                }
                result = control.epoch as isize;
            }
            2 => {
                if control.owner != pid {
                    return_errno!(Errno::EPERM);
                }
                if control.epoch == 0 || control.case != arg3 || arg4 != 0 {
                    return_errno!(Errno::EINVAL);
                }
                if control.running {
                    ENABLED.store(0, SeqCst);
                    control.running = false;
                    control.freeze = read_tsc();
                }
                if active() != 0 {
                    return_errno!(Errno::EBUSY);
                }
                if control.quiescent == 0 {
                    control.quiescent = read_tsc();
                }
                result = BYTES as isize;
            }
            3 => {
                if control.owner != pid {
                    return_errno!(Errno::EPERM);
                }
                if control.running || control.quiescent == 0 || active() != 0 {
                    return_errno!(Errno::EBUSY);
                }
                raw[..HWORDS].copy_from_slice(&[
                    MAGIC,
                    VERSION,
                    HWORDS as u64,
                    CPUS as u64,
                    KINDS as u64,
                    FIELDS as u64,
                    tsc_freq(),
                    control.epoch,
                    control.case,
                    control.mode,
                    control.start,
                    control.freeze,
                    control.quiescent,
                    0,
                    ostd::cpu::num_cpus().saturating_sub(CPUS) as u64,
                    0,
                ]);
                for (cpu_index, cpu) in COUNTERS.iter().enumerate() {
                    for (kind, row) in cpu.rows.iter().enumerate() {
                        let offset = HWORDS + (cpu_index * KINDS + kind) * FIELDS;
                        for (field, counter) in row.0.iter().enumerate() {
                            raw[offset + field] = counter.load(Relaxed);
                        }
                    }
                }
                result = BYTES as isize;
            }
            _ => unreachable!(),
        }
    }
    if command == 3 {
        ctx.user_space().write_slice(arg3 as usize, &raw)?;
    }
    Ok(result)
}
