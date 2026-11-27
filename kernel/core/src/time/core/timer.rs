// SPDX-License-Identifier: MPL-2.0

use alloc::{
    collections::BTreeMap,
    sync::{Arc, Weak},
    vec::Vec,
};
use core::{any::Any, time::Duration};

use ostd::sync::{LocalIrqDisabled, SpinLock, SpinLockGuard};

use super::Clock;

/// A timeout, represented in one of the two ways.
#[derive(Clone, Debug)]
pub(crate) enum Timeout {
    /// The timeout is reached _after_ the `Duration` time is elapsed.
    After(Duration),
    /// The timeout is reached _when_ the clock's time is equal to `Duration`.
    When(Duration),
}

/// A timer with periodic functionality.
///
/// Setting the timer will trigger a callback function upon expiration of
/// the set time. To enable its periodic functionality, users should set
/// its `interval` field with [`TimerGuard::set_interval`]. By doing this,
/// the timer will use the interval time to configure a new timing after expiration.
///
/// Each timer has at most one pending callback in its [`TimerManager`].
/// Cancelling the timer, setting a new timeout, or dropping the last reference
/// removes any queued callback immediately. Callbacks run with a temporary
/// [`Arc`] held by the dispatcher, so callers that need to stop a periodic
/// timer before dropping it should call [`TimerGuard::cancel`] first.
pub(crate) struct Timer<F: ?Sized = dyn TimerFn> {
    inner: SpinLock<TimerInner>,
    timer_manager: Arc<TimerManager>,
    registered_callback: F,
}

#[derive(Default)]
struct TimerInner {
    // Identifies the active registration, whether queued or popped for dispatch.
    // A `None` denotes that no registration is armed.
    pending_key: Option<TimerKey>,
    // A `Duration::ZERO` denotes that the timer won't repeat.
    interval: Duration,
}

/// Unique key identifying a single armed registration in a [`TimerManager`].
///
/// Ordering by `expired_time` first keeps the earliest deadline at the front of
/// the queue. The monotonic `id` keeps equal-deadline timers distinct and
/// separates a popped stale callback from a later arming at the same deadline.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct TimerKey {
    expired_time: Duration,
    id: u64,
}

impl<F: TimerFn> Timer<F> {
    /// Creates a timer instance from a [`TimerManager`].
    ///
    /// This timer will be managed by the [`TimerManager`].
    ///
    /// Note that if the callback instructions involves sleep, users should put these instructions
    /// into something like [`WorkQueue`] to avoid sleeping during system timer interruptions.
    ///
    /// [`WorkQueue`]: crate::thread::work_queue::WorkQueue
    fn new(registered_callback: F, timer_manager: Arc<TimerManager>) -> Arc<Self> {
        Arc::new(Self {
            inner: SpinLock::new(TimerInner::default()),
            timer_manager,
            registered_callback,
        })
    }

    /// Returns a reference to the function that will be called when the timer expires.
    pub(crate) fn func(&self) -> &F {
        &self.registered_callback
    }
}

impl<F: ?Sized + TimerFn> Timer<F> {
    /// Locks the timer and returns a [`TimerGuard`] for exclusive access.
    pub(crate) fn lock(self: &Arc<Self>) -> TimerGuard<'_, F> {
        TimerGuard {
            inner: self.inner.disable_irq().lock(),
            timer: self,
        }
    }

    /// Returns a reference to the [`TimerManager`] which manages
    /// the current timer.
    pub(crate) fn timer_manager(&self) -> &Arc<TimerManager> {
        &self.timer_manager
    }
}

impl<F: ?Sized> Drop for Timer<F> {
    fn drop(&mut self) {
        // Exclusive `&mut self` guarantees that no `TimerGuard` exists.
        if let Some(key) = self.inner.get_mut().pending_key.take() {
            self.timer_manager.remove(key);
        }
    }
}

/// A guard that provides exclusive access to a `Timer`.
pub(crate) struct TimerGuard<'a, F: ?Sized = dyn TimerFn> {
    inner: SpinLockGuard<'a, TimerInner, LocalIrqDisabled>,
    timer: &'a Arc<Timer<F>>,
}

impl<F: TimerFn> TimerGuard<'_, F> {
    /// Sets the timer with a timeout.
    ///
    /// The registered callback function of this timer will be invoked
    /// when reaching timeout. If the timer has a valid interval, this timer
    /// will be set again with the interval when reaching timeout.
    ///
    /// Replacing an existing timeout retires the previous registration immediately.
    pub(crate) fn set_timeout(&mut self, timeout: Timeout) {
        self.set_timeout_impl(timeout, Arc::downgrade(self.timer) as _);
    }
}

impl TimerGuard<'_> {
    /// Sets the timer with a timeout.
    ///
    /// The registered callback function of this timer will be invoked
    /// when reaching timeout. If the timer has a valid interval, this timer
    /// will be set again with the interval when reaching timeout.
    ///
    /// Replacing an existing timeout retires the previous registration immediately.
    pub(crate) fn set_timeout(&mut self, timeout: Timeout) {
        self.set_timeout_impl(timeout, Arc::downgrade(self.timer));
    }
}

impl<F: ?Sized + TimerFn> TimerGuard<'_, F> {
    fn set_timeout_impl(&mut self, timeout: Timeout, timer_weak: Weak<Timer>) {
        let expired_time = match timeout {
            Timeout::After(timeout) => {
                let now = self.timer.timer_manager.clock.read_time();
                now + timeout
            }
            Timeout::When(timeout) => timeout,
        };

        let old_key = self.inner.pending_key.take();
        let new_key = self
            .timer
            .timer_manager
            .reschedule(old_key, expired_time, timer_weak);
        self.inner.pending_key = Some(new_key);
    }
}

impl<F: ?Sized + TimerFn> TimerGuard<'_, F> {
    /// Sets the interval time for this timer.
    ///
    /// The timer will be reset with the interval time upon expiration.
    pub(crate) fn set_interval(&mut self, interval: Duration) {
        self.inner.interval = interval;
    }

    /// Cancels the timer.
    ///
    /// Once cancelled, the timer will not be triggered again.
    pub(crate) fn cancel(&mut self) {
        if let Some(key) = self.inner.pending_key.take() {
            self.timer.timer_manager.remove(key);
        }
    }

    /// Returns the current expired time of this timer.
    ///
    /// If the timer has not been set, this method
    /// will return a [`Duration::ZERO`].
    pub(crate) fn expired_time(&self) -> Duration {
        self.inner
            .pending_key
            .map_or(Duration::ZERO, |key| key.expired_time)
    }

    /// Returns the remain time to expiration of this timer.
    ///
    /// If the timer has not been set, this method
    /// will return a [`Duration::ZERO`].
    pub(crate) fn remain(&self) -> Duration {
        let Some(key) = self.inner.pending_key else {
            return Duration::ZERO;
        };
        let now = self.timer.timer_manager.clock.read_time();
        key.expired_time.saturating_sub(now)
    }

    /// Returns the interval time of the current timer.
    pub(crate) fn interval(&self) -> Duration {
        self.inner.interval
    }
}

/// A function that will be called when a timer expires.
pub(crate) trait TimerFn: Send + Sync + Any {
    /// Calls the function.
    fn call(&self, guard: TimerGuard);
}

impl<T> TimerFn for T
where
    T: Fn(TimerGuard) + Send + Sync + 'static,
{
    fn call(&self, guard: TimerGuard) {
        (self)(guard)
    }
}

/// A manager that can be used to create timers and manage their expiration.
///
/// It holds a [`Clock`] and can create [`Timer`]s based on this clock.
///
/// These created [`Timer`]s will hold an [`Arc`] pointer to this manager, hence this
/// manager will be actually dropped after all the created timers have been dropped.
///
/// # Lock ordering
///
/// Lock order is `Timer::inner` -> `TimerManager::timer_callbacks` -> `Clock`.
/// Never lock a [`Timer`] or drop an `Arc<Timer>` while holding `timer_callbacks`.
pub(crate) struct TimerManager {
    clock: Arc<dyn Clock>,
    timer_callbacks: SpinLock<TimerQueue>,
}

#[derive(Default)]
struct TimerQueue {
    // Monotonically incremented and never reused so a popped key cannot match
    // a later arming of the same timer even at the same deadline.
    next_id: u64,
    callbacks: BTreeMap<TimerKey, Weak<Timer>>,
}

impl TimerManager {
    /// Creates a `TimerManager` instance from a clock.
    pub(crate) fn new(clock: Arc<dyn Clock>) -> Arc<Self> {
        Arc::new(Self {
            clock,
            timer_callbacks: SpinLock::new(TimerQueue::default()),
        })
    }

    /// Returns the clock associated with this timer manager.
    pub(crate) fn clock(&self) -> &Arc<dyn Clock> {
        &self.clock
    }

    /// Returns whether a given `timeout` is expired.
    pub(crate) fn is_expired_timeout(&self, timeout: &Timeout) -> bool {
        match timeout {
            Timeout::After(duration) => *duration == Duration::ZERO,
            Timeout::When(duration) => {
                let now = self.clock.read_time();
                now >= *duration
            }
        }
    }

    /// Replaces `old_key`, if any, with a new registration for `timer` at
    /// `expired_time` under a single queue lock acquisition.
    fn reschedule(
        &self,
        old_key: Option<TimerKey>,
        expired_time: Duration,
        timer: Weak<Timer>,
    ) -> TimerKey {
        let (key, removed) = {
            let mut queue = self.timer_callbacks.disable_irq().lock();
            let removed = old_key.and_then(|old_key| queue.callbacks.remove(&old_key));
            let id = queue.next_id;
            queue.next_id = id.checked_add(1).expect("timer callback ID overflow");
            let key = TimerKey { expired_time, id };
            let replaced = queue.callbacks.insert(key, timer);
            debug_assert!(replaced.is_none());
            (key, removed)
        };
        // Drop the removed weak handle after releasing `timer_callbacks`.
        drop(removed);
        key
    }

    /// Removes the queued callback identified by `key`, if it is still present.
    fn remove(&self, key: TimerKey) {
        let removed = {
            self.timer_callbacks
                .disable_irq()
                .lock()
                .callbacks
                .remove(&key)
        };
        drop(removed);
    }

    /// Checks and processes the managed timers.
    ///
    /// If any of the timers have timed out, call the corresponding callback functions.
    pub(crate) fn process_expired_timers(&self) {
        let callbacks = {
            let mut queue = self.timer_callbacks.disable_irq().lock();
            if queue.callbacks.is_empty() {
                return;
            }

            let mut callbacks = Vec::new();
            let current_time = self.clock.read_time();
            while let Some(entry) = queue.callbacks.first_entry() {
                if entry.key().expired_time <= current_time {
                    let (key, timer) = entry.remove_entry();
                    callbacks.push(TimerCallback { key, timer });
                } else {
                    break;
                }
            }
            callbacks
        };

        for callback in callbacks {
            callback.call();
        }
    }

    /// Creates an [`Timer`], which will be managed by this `TimerManager`.
    pub(crate) fn create_timer<F: TimerFn>(self: &Arc<Self>, function: F) -> Arc<Timer<F>> {
        Timer::new(function, self.clone())
    }
}

/// A `TimerCallback` can be used to execute a timer callback function.
struct TimerCallback {
    key: TimerKey,
    timer: Weak<Timer>,
}

impl TimerCallback {
    fn call(self) {
        let Some(timer_arc) = self.timer.upgrade() else {
            return;
        };

        let mut timer_guard = timer_arc.lock();

        if timer_guard.inner.pending_key != Some(self.key) {
            // The callback was cancelled or superseded after leaving the queue.
            return;
        }

        // `process_expired_timers` already removed `key` from `timer_callbacks`.
        timer_guard.inner.pending_key = None;

        let interval = timer_guard.interval();
        if interval != Duration::ZERO {
            timer_guard.set_timeout(Timeout::After(interval));
        }

        // Pass the `timer_guard` guard to the callback, allowing it to prevent race conditions.
        // The callback may choose to use the guard or drop it if not needed.
        timer_arc.registered_callback.call(timer_guard);
    }
}

#[cfg(ktest)]
mod tests {
    use core::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};

    use ostd::{
        cpu::{self, CpuSet, PinCurrentCpu},
        irq,
        prelude::ktest,
        smp,
        util::id_set::Id,
    };

    use super::*;

    struct TestClock(AtomicU64);

    impl TestClock {
        fn new() -> Arc<Self> {
            Arc::new(Self(AtomicU64::new(0)))
        }

        fn advance(&self, delta: Duration) {
            self.0.fetch_add(delta.as_nanos() as u64, Ordering::Relaxed);
        }
    }

    impl Clock for TestClock {
        fn read_time(&self) -> Duration {
            Duration::from_nanos(self.0.load(Ordering::Relaxed))
        }
    }

    struct DropGuard(Arc<AtomicUsize>);

    impl Drop for DropGuard {
        fn drop(&mut self) {
            self.0.fetch_add(1, Ordering::Release);
        }
    }

    fn queued_callback_count(manager: &TimerManager) -> usize {
        manager.timer_callbacks.disable_irq().lock().callbacks.len()
    }

    fn is_armed<F: ?Sized + TimerFn>(timer: &Timer<F>) -> bool {
        timer.inner.disable_irq().lock().pending_key.is_some()
    }

    fn counting_timer(
        manager: &Arc<TimerManager>,
        fired: &Arc<AtomicUsize>,
    ) -> Arc<Timer<impl TimerFn>> {
        let fired = fired.clone();
        manager.create_timer(move |_: TimerGuard<'_>| {
            fired.fetch_add(1, Ordering::Relaxed);
        })
    }

    #[ktest]
    fn dropping_owners_retires_callbacks_behind_earlier_live_timer() {
        const DROPPED_TIMERS: usize = 64;

        let clock = TestClock::new();
        let manager = TimerManager::new(clock.clone());

        for deadline in [Duration::ZERO, Duration::from_secs(3600)] {
            let dropped = Arc::new(AtomicUsize::new(0));
            for _ in 0..DROPPED_TIMERS {
                let guard = DropGuard(dropped.clone());
                let timer = manager.create_timer(move |_: TimerGuard<'_>| {
                    let _keep = &guard;
                    panic!("dropped timer fired");
                });
                timer.lock().set_timeout(Timeout::When(deadline));
            }
            assert_eq!(dropped.load(Ordering::Relaxed), DROPPED_TIMERS);
            assert_eq!(queued_callback_count(&manager), 0);
        }

        // An earlier live timer stays at the front of the queue while later timers drop.
        let live_fired = Arc::new(AtomicUsize::new(0));
        let live = counting_timer(&manager, &live_fired);
        live.lock()
            .set_timeout(Timeout::When(Duration::from_millis(10)));

        let dropped = Arc::new(AtomicUsize::new(0));
        let stale_fired = Arc::new(AtomicUsize::new(0));
        let mut temporaries = Vec::with_capacity(DROPPED_TIMERS);
        for index in 0..DROPPED_TIMERS {
            let guard = DropGuard(dropped.clone());
            let stale_fired = stale_fired.clone();
            let timer = manager.create_timer(move |_: TimerGuard<'_>| {
                let _keep = &guard;
                stale_fired.fetch_add(1, Ordering::Relaxed);
            });
            timer
                .lock()
                .set_timeout(Timeout::When(Duration::from_millis(20 + index as u64)));
            temporaries.push(timer);
        }
        assert_eq!(queued_callback_count(&manager), 1 + DROPPED_TIMERS);

        drop(temporaries);
        assert_eq!(dropped.load(Ordering::Relaxed), DROPPED_TIMERS);
        assert_eq!(queued_callback_count(&manager), 1);

        clock.advance(Duration::from_millis(200));
        manager.process_expired_timers();
        assert_eq!(live_fired.load(Ordering::Relaxed), 1);
        assert_eq!(stale_fired.load(Ordering::Relaxed), 0);
        assert_eq!(queued_callback_count(&manager), 0);
    }

    #[ktest]
    fn rearming_and_cancelling_retire_old_callbacks() {
        let clock = TestClock::new();
        let manager = TimerManager::new(clock.clone());
        let fired = Arc::new(AtomicUsize::new(0));
        let timer = counting_timer(&manager, &fired);

        assert_eq!(Arc::weak_count(&timer), 0);
        assert_eq!(timer.lock().remain(), Duration::ZERO);

        for deadline_ms in [20, 50, 30, 40, 40] {
            timer
                .lock()
                .set_timeout(Timeout::When(Duration::from_millis(deadline_ms)));
            assert_eq!(queued_callback_count(&manager), 1);
            assert_eq!(Arc::weak_count(&timer), 1);
        }

        clock.advance(Duration::from_millis(35));
        manager.process_expired_timers();
        assert_eq!(fired.load(Ordering::Relaxed), 0);
        assert_eq!(timer.lock().remain(), Duration::from_millis(5));
        assert_eq!(queued_callback_count(&manager), 1);

        clock.advance(Duration::from_millis(5));
        manager.process_expired_timers();
        assert_eq!(fired.load(Ordering::Relaxed), 1);
        assert_eq!(queued_callback_count(&manager), 0);
        assert_eq!(timer.lock().remain(), Duration::ZERO);

        timer
            .lock()
            .set_timeout(Timeout::When(Duration::from_millis(100)));
        assert_eq!(queued_callback_count(&manager), 1);
        timer.lock().cancel();
        assert_eq!(queued_callback_count(&manager), 0);
        assert_eq!(Arc::weak_count(&timer), 0);
        assert_eq!(timer.lock().expired_time(), Duration::ZERO);
        assert_eq!(timer.lock().remain(), Duration::ZERO);

        // Cancelling an already disarmed timer is a no-op.
        timer.lock().cancel();
        clock.advance(Duration::from_millis(100));
        manager.process_expired_timers();
        assert_eq!(fired.load(Ordering::Relaxed), 1);
        assert_eq!(queued_callback_count(&manager), 0);
    }

    #[ktest]
    fn equal_deadlines_keep_distinct_timers() {
        let clock = TestClock::new();
        let manager = TimerManager::new(clock.clone());
        let shared_deadline = Duration::from_millis(50);

        let fired = [
            Arc::new(AtomicUsize::new(0)),
            Arc::new(AtomicUsize::new(0)),
            Arc::new(AtomicUsize::new(0)),
            Arc::new(AtomicUsize::new(0)),
        ];
        let dropped = Arc::new(AtomicUsize::new(0));

        let t0 = counting_timer(&manager, &fired[0]);
        let t1 = {
            let fired = fired[1].clone();
            let guard = DropGuard(dropped.clone());
            manager.create_timer(move |_: TimerGuard<'_>| {
                let _keep = &guard;
                fired.fetch_add(1, Ordering::Relaxed);
            })
        };
        let t2 = counting_timer(&manager, &fired[2]);
        let t3 = counting_timer(&manager, &fired[3]);

        t0.lock().set_timeout(Timeout::When(shared_deadline));
        t1.lock().set_timeout(Timeout::When(shared_deadline));
        t2.lock().set_timeout(Timeout::When(shared_deadline));
        t3.lock().set_timeout(Timeout::When(shared_deadline));
        assert_eq!(queued_callback_count(&manager), 4);

        t0.lock().cancel();
        drop(t1);
        t2.lock()
            .set_timeout(Timeout::When(Duration::from_millis(80)));
        assert_eq!(dropped.load(Ordering::Relaxed), 1);
        assert_eq!(queued_callback_count(&manager), 2);

        clock.advance(Duration::from_millis(50));
        manager.process_expired_timers();
        assert_eq!(fired[0].load(Ordering::Relaxed), 0);
        assert_eq!(fired[1].load(Ordering::Relaxed), 0);
        assert_eq!(fired[2].load(Ordering::Relaxed), 0);
        assert_eq!(fired[3].load(Ordering::Relaxed), 1);
        assert_eq!(queued_callback_count(&manager), 1);

        clock.advance(Duration::from_millis(30));
        manager.process_expired_timers();
        assert_eq!(fired[2].load(Ordering::Relaxed), 1);
        assert_eq!(queued_callback_count(&manager), 0);
    }

    #[ktest]
    fn dequeued_callbacks_respect_cancel_drop_and_same_deadline_reset() {
        let clock = TestClock::new();
        let manager = TimerManager::new(clock.clone());
        let deadline = Duration::from_millis(10);

        let first_fired = Arc::new(AtomicUsize::new(0));
        let cancel_fired = Arc::new(AtomicUsize::new(0));
        let drop_fired = Arc::new(AtomicUsize::new(0));
        let reset_fired = Arc::new(AtomicUsize::new(0));
        let dropped = Arc::new(AtomicUsize::new(0));

        let t_cancel = counting_timer(&manager, &cancel_fired);
        let t_drop: Arc<Timer> = {
            let drop_fired = drop_fired.clone();
            let guard = DropGuard(dropped.clone());
            manager.create_timer(move |_: TimerGuard<'_>| {
                let _keep = &guard;
                drop_fired.fetch_add(1, Ordering::Relaxed);
            })
        };
        let t_reset = counting_timer(&manager, &reset_fired);

        t_cancel.lock().set_timeout(Timeout::When(deadline));
        t_drop.lock().set_timeout(Timeout::When(deadline));
        t_reset.lock().set_timeout(Timeout::When(deadline));

        let drop_holder = Arc::new(SpinLock::new(Some(t_drop)));
        let t_first = {
            let first_fired = first_fired.clone();
            let t_cancel = t_cancel.clone();
            let drop_holder = drop_holder.clone();
            let t_reset = t_reset.clone();
            manager.create_timer(move |_: TimerGuard<'_>| {
                first_fired.fetch_add(1, Ordering::Relaxed);
                // All three later timers are already popped in the same expiration batch.
                t_cancel.lock().cancel();
                drop(drop_holder.disable_irq().lock().take());
                t_reset.lock().set_timeout(Timeout::When(deadline));
            })
        };
        t_first
            .lock()
            .set_timeout(Timeout::When(Duration::from_millis(5)));

        clock.advance(deadline);
        manager.process_expired_timers();

        assert_eq!(first_fired.load(Ordering::Relaxed), 1);
        assert_eq!(cancel_fired.load(Ordering::Relaxed), 0);
        assert_eq!(drop_fired.load(Ordering::Relaxed), 0);
        assert_eq!(dropped.load(Ordering::Relaxed), 1);
        assert_eq!(reset_fired.load(Ordering::Relaxed), 0);
        assert_eq!(t_reset.lock().expired_time(), deadline);
        assert_eq!(queued_callback_count(&manager), 1);

        // The newly armed generation at the same deadline runs once on the next pass.
        manager.process_expired_timers();
        assert_eq!(reset_fired.load(Ordering::Relaxed), 1);
        assert_eq!(t_reset.lock().expired_time(), Duration::ZERO);
        assert_eq!(queued_callback_count(&manager), 0);
    }

    #[ktest]
    fn periodic_timer_rearms_and_stops_on_cancel_or_drop() {
        let clock = TestClock::new();
        let manager = TimerManager::new(clock.clone());
        let period = Duration::from_millis(10);

        // Drop between ticks retires the newly scheduled period immediately.
        let between_fired = Arc::new(AtomicUsize::new(0));
        let between_dropped = Arc::new(AtomicUsize::new(0));
        let between_timer = {
            let between_fired = between_fired.clone();
            let guard = DropGuard(between_dropped.clone());
            manager.create_timer(move |_: TimerGuard<'_>| {
                let _keep = &guard;
                between_fired.fetch_add(1, Ordering::Relaxed);
            })
        };
        {
            let mut guard = between_timer.lock();
            guard.set_interval(period);
            guard.set_timeout(Timeout::After(period));
        }
        for expected in 1..=2 {
            clock.advance(period);
            manager.process_expired_timers();
            assert_eq!(between_fired.load(Ordering::Relaxed), expected);
            assert_eq!(queued_callback_count(&manager), 1);
        }
        drop(between_timer);
        assert_eq!(between_dropped.load(Ordering::Relaxed), 1);
        assert_eq!(queued_callback_count(&manager), 0);
        clock.advance(Duration::from_millis(50));
        manager.process_expired_timers();
        assert_eq!(between_fired.load(Ordering::Relaxed), 2);

        // Cancelling inside the callback removes the next scheduled period immediately.
        let cancel_fired = Arc::new(AtomicUsize::new(0));
        let cancel_timer = {
            let cancel_fired = cancel_fired.clone();
            manager.create_timer(move |mut guard: TimerGuard<'_>| {
                if cancel_fired.fetch_add(1, Ordering::Relaxed) + 1 == 2 {
                    guard.cancel();
                }
            })
        };
        {
            let mut guard = cancel_timer.lock();
            guard.set_interval(period);
            guard.set_timeout(Timeout::After(period));
        }
        clock.advance(period);
        manager.process_expired_timers();
        assert_eq!(cancel_fired.load(Ordering::Relaxed), 1);
        assert_eq!(queued_callback_count(&manager), 1);
        clock.advance(period);
        manager.process_expired_timers();
        assert_eq!(cancel_fired.load(Ordering::Relaxed), 2);
        assert_eq!(queued_callback_count(&manager), 0);
        clock.advance(Duration::from_millis(50));
        manager.process_expired_timers();
        assert_eq!(cancel_fired.load(Ordering::Relaxed), 2);

        // Dropping the last external reference inside the callback retires the next
        // period as soon as dispatch finishes.
        let drop_fired = Arc::new(AtomicUsize::new(0));
        let drop_reclaimed = Arc::new(AtomicUsize::new(0));
        let holder: Arc<SpinLock<Option<Arc<Timer>>>> = Arc::new(SpinLock::new(None));
        let drop_timer: Arc<Timer> = {
            let drop_fired = drop_fired.clone();
            let holder = Arc::downgrade(&holder);
            let guard = DropGuard(drop_reclaimed.clone());
            manager.create_timer(move |_: TimerGuard<'_>| {
                let _keep = &guard;
                if drop_fired.fetch_add(1, Ordering::Relaxed) + 1 == 2
                    && let Some(holder) = holder.upgrade()
                {
                    drop(holder.disable_irq().lock().take());
                }
            })
        };
        {
            let mut guard = drop_timer.lock();
            guard.set_interval(period);
            guard.set_timeout(Timeout::After(period));
        }
        *holder.disable_irq().lock() = Some(drop_timer);

        clock.advance(period);
        manager.process_expired_timers();
        assert_eq!(drop_fired.load(Ordering::Relaxed), 1);
        assert_eq!(drop_reclaimed.load(Ordering::Relaxed), 0);
        assert_eq!(queued_callback_count(&manager), 1);

        clock.advance(period);
        manager.process_expired_timers();
        assert_eq!(drop_fired.load(Ordering::Relaxed), 2);
        assert_eq!(drop_reclaimed.load(Ordering::Relaxed), 1);
        assert_eq!(queued_callback_count(&manager), 0);

        clock.advance(Duration::from_millis(50));
        manager.process_expired_timers();
        assert_eq!(drop_fired.load(Ordering::Relaxed), 2);
        assert_eq!(queued_callback_count(&manager), 0);
    }

    struct ConcurrentTimers {
        manager: Arc<TimerManager>,
        shared: Arc<Timer>,
        observations: SpinLock<Vec<Arc<AtomicUsize>>>,
        dropped: Arc<AtomicUsize>,
        completed: Vec<AtomicUsize>,
    }

    static CONCURRENT_TIMERS: SpinLock<Option<Arc<ConcurrentTimers>>> = SpinLock::new(None);
    const ROUNDS: usize = 32;
    const ITERS_PER_ROUND: usize = 16;

    struct ConcurrentTimersReset;

    impl Drop for ConcurrentTimersReset {
        fn drop(&mut self) {
            CONCURRENT_TIMERS.disable_irq().lock().take();
        }
    }

    fn exercise_concurrent_timers() {
        let Some(state) = CONCURRENT_TIMERS.disable_irq().lock().as_ref().cloned() else {
            return;
        };
        for iteration in 0..ITERS_PER_ROUND {
            if iteration.is_multiple_of(2) {
                // Use a future deadline on the final even iteration so only the
                // subsequent `cancel` can disarm `shared`, not `process_expired_timers`.
                let deadline = if iteration == ITERS_PER_ROUND - 2 {
                    Duration::from_secs(1)
                } else {
                    Duration::ZERO
                };
                state.shared.lock().set_timeout(Timeout::When(deadline));
            } else {
                state.shared.lock().cancel();
            }

            let fired = Arc::new(AtomicUsize::new(0));
            state.observations.disable_irq().lock().push(fired.clone());
            let guard = DropGuard(state.dropped.clone());
            let timer = state.manager.create_timer(move |_: TimerGuard<'_>| {
                let _keep = &guard;
                fired.fetch_add(1, Ordering::Relaxed);
            });
            {
                let mut timer_guard = timer.lock();
                timer_guard.set_timeout(Timeout::When(Duration::ZERO));
                timer_guard.set_timeout(Timeout::When(Duration::ZERO));
            }
            if iteration.is_multiple_of(3) {
                timer.lock().cancel();
            } else {
                state.manager.process_expired_timers();
            }
            drop(timer);
        }
        let cpu = irq::disable_local().current_cpu();
        state.completed[cpu.as_usize()].fetch_add(ITERS_PER_ROUND, Ordering::Release);
    }

    #[ktest]
    fn concurrent_rearm_cancel_and_dispatch_retire_and_fire_at_most_once() {
        // The ktest FIFO scheduler runs spawned threads on the BSP only, so use IPIs
        // to run on every CPU. True parallel contention requires `SMP>=2`, while
        // `SMP=1` still exercises this harness synchronously on the BSP.
        const SENTINEL_COUNT: usize = 64;
        let clock = TestClock::new();
        let manager = TimerManager::new(clock);
        let sentinel_fired = Arc::new(AtomicBool::new(false));
        let mut sentinels = Vec::with_capacity(SENTINEL_COUNT);
        for _ in 0..SENTINEL_COUNT {
            let sentinel_fired = sentinel_fired.clone();
            let timer = manager.create_timer(move |_: TimerGuard<'_>| {
                sentinel_fired.store(true, Ordering::Release);
            });
            timer
                .lock()
                .set_timeout(Timeout::When(Duration::from_secs(1)));
            sentinels.push(timer);
        }

        let total_timers = cpu::num_cpus() * ROUNDS * ITERS_PER_ROUND;
        let shared: Arc<Timer> = manager.create_timer(|_: TimerGuard<'_>| {});
        let dropped = Arc::new(AtomicUsize::new(0));
        let state = Arc::new(ConcurrentTimers {
            manager: manager.clone(),
            shared,
            observations: SpinLock::new(Vec::with_capacity(total_timers)),
            dropped: dropped.clone(),
            completed: (0..cpu::num_cpus()).map(|_| AtomicUsize::new(0)).collect(),
        });
        let _reset_guard = ConcurrentTimersReset;
        *CONCURRENT_TIMERS.disable_irq().lock() = Some(state.clone());

        for _ in 0..ROUNDS {
            smp::inter_processor_call(&CpuSet::new_full(), exercise_concurrent_timers).wait();
            assert!(
                !sentinel_fired.load(Ordering::Acquire),
                "future sentinel timer fired"
            );
            // The final even iteration arms `shared` in the future and every CPU's final
            // iteration cancels it, so `shared` can only finish disarmed via `cancel`.
            assert!(!is_armed(&state.shared));
            assert_eq!(queued_callback_count(&manager), sentinels.len());
        }
        drop(_reset_guard);

        for (cpu_idx, count) in state.completed.iter().enumerate() {
            assert_eq!(
                count.load(Ordering::Acquire),
                ROUNDS * ITERS_PER_ROUND,
                "CPU {cpu_idx} did not finish"
            );
        }
        let observations = core::mem::take(&mut *state.observations.disable_irq().lock());
        assert_eq!(observations.len(), total_timers);
        for (index, count) in observations.iter().enumerate() {
            assert!(
                count.load(Ordering::Relaxed) <= 1,
                "callback {index} fired more than once"
            );
        }
        assert!(
            observations
                .iter()
                .any(|count| count.load(Ordering::Relaxed) == 1),
            "no callback fired during concurrent dispatch"
        );
        drop(observations);
        drop(state);
        drop(sentinels);
        assert_eq!(dropped.load(Ordering::Acquire), total_timers);
        assert_eq!(queued_callback_count(&manager), 0);
    }
}
