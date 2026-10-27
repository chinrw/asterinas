// SPDX-License-Identifier: MPL-2.0

use alloc::{
    boxed::Box,
    collections::BinaryHeap,
    sync::{Arc, Weak},
    vec::Vec,
};
use core::{
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};

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
pub(crate) struct Timer {
    inner: SpinLock<TimerInner>,
    timer_manager: Arc<TimerManager>,
    registered_callback: Box<dyn Fn(TimerGuard) + Send + Sync>,
}

#[derive(Default)]
struct TimerInner {
    interval: Duration,
    timer_callback: Weak<TimerCallback>,
}

/// A guard that provides exclusive access to a `Timer`.
pub(crate) struct TimerGuard<'a> {
    inner: SpinLockGuard<'a, TimerInner, LocalIrqDisabled>,
    timer: &'a Arc<Timer>,
}

impl TimerGuard<'_> {
    /// Sets the interval time for this timer.
    ///
    /// The timer will be reset with the interval time upon expiration.
    pub(crate) fn set_interval(&mut self, interval: Duration) {
        self.inner.interval = interval;
    }

    /// Sets the timer with a timeout.
    ///
    /// The registered callback function of this timer will be invoked
    /// when reaching timeout. If the timer has a valid interval, this timer
    /// will be set again with the interval when reaching timeout.
    pub(crate) fn set_timeout(&mut self, timeout: Timeout) {
        let expired_time = match timeout {
            Timeout::After(timeout) => {
                let now = self.timer.timer_manager.clock.read_time();
                now + timeout
            }
            Timeout::When(timeout) => timeout,
        };

        let timer_weak = Arc::downgrade(self.timer);
        let new_timer_callback = Arc::new(TimerCallback::new(expired_time, timer_weak));

        if let Some(timer_callback) = self.inner.timer_callback.upgrade() {
            timer_callback.cancel();
        }

        self.inner.timer_callback = Arc::downgrade(&new_timer_callback);
        self.timer.timer_manager.insert(new_timer_callback);
    }

    /// Cancels the currently set `TimerCallback`.
    ///
    /// Once cancelled, the current `TimerCallback` will not be triggered again.
    pub(crate) fn cancel(&self) {
        if let Some(timer_callback) = self.inner.timer_callback.upgrade() {
            timer_callback.cancel();
        }
    }

    /// Returns the current expired time of this timer.
    pub(crate) fn expired_time(&self) -> Duration {
        let timer_callback = self.inner.timer_callback.upgrade();
        timer_callback
            .and_then(|callback| (!callback.is_cancelled()).then_some(callback.expired_time))
            .unwrap_or(Duration::ZERO)
    }

    /// Returns the remain time to expiration of this timer.
    ///
    /// If the timer has not been set, this method
    /// will return `Duration::ZERO`.
    pub(crate) fn remain(&self) -> Duration {
        let now = self.timer.timer_manager.clock.read_time();
        let expired_time = self.expired_time();
        if expired_time > now {
            expired_time - now
        } else {
            Duration::ZERO
        }
    }

    /// Returns the interval time of the current timer.
    pub(crate) fn interval(&self) -> Duration {
        self.inner.interval
    }
}

impl Timer {
    /// Creates a `Timer` instance from a [`TimerManager`].
    /// This timer will be managed by the `TimerManager`.
    ///
    /// Note that if the callback instructions involves sleep, users should put these instructions
    /// into something like `WorkQueue` to avoid sleeping during system timer interruptions.
    fn new<F>(registered_callback: F, timer_manager: Arc<TimerManager>) -> Arc<Self>
    where
        F: Fn(TimerGuard) + Send + Sync + 'static,
    {
        Arc::new(Self {
            inner: SpinLock::new(TimerInner::default()),
            timer_manager,
            registered_callback: Box::new(registered_callback),
        })
    }

    /// Locks the timer and returns a [`TimerGuard`] for exclusive access.
    pub(crate) fn lock(self: &Arc<Self>) -> TimerGuard<'_> {
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

/// `TimerManager` is used to create timers and manage their expiries. It holds a clock and can
/// create [`Timer`]s based on this clock.
///
/// These created `Timer`s will hold an `Arc` pointer to this manager, hence this manager
/// will be actually dropped after all the created timers have been dropped.
pub(crate) struct TimerManager {
    clock: Arc<dyn Clock>,
    timer_callbacks: SpinLock<BinaryHeap<Arc<TimerCallback>>>,
}

impl TimerManager {
    /// Creates a `TimerManager` instance from a clock.
    pub(crate) fn new(clock: Arc<dyn Clock>) -> Arc<Self> {
        Arc::new(Self {
            clock,
            timer_callbacks: SpinLock::new(BinaryHeap::new()),
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

    fn insert(&self, timer_callback: Arc<TimerCallback>) {
        let diagnostic_start = crate::runtime_timeline::timer_operation_start();
        let mut callbacks = self.timer_callbacks.disable_irq().lock();
        callbacks.push(timer_callback);
        crate::runtime_timeline::timer_inserted(
            diagnostic_start,
            callbacks.len(),
            self as *const Self as u64,
        );
    }

    /// Checks and processes the managed timers.
    ///
    /// If any of the timers have timed out, call the corresponding callback functions.
    pub(crate) fn process_expired_timers(&self) {
        self.process_expired_timers_with_budget(usize::MAX);
    }

    /// Processes at most `budget` expired or cancelled entries.
    ///
    /// Returns whether expired or cancelled entries remain to be processed.
    pub(crate) fn process_expired_timers_with_budget(&self, budget: usize) -> bool {
        let diagnostic_start = crate::runtime_timeline::timer_operation_start();
        let (callbacks, has_pending) = {
            let mut timeout_list = self.timer_callbacks.disable_irq().lock();
            if timeout_list.is_empty() {
                return false;
            }

            let before = timeout_list.len();
            let mut cancelled = 0;
            let mut callbacks = Vec::new();
            let current_time = self.clock.read_time();
            for _ in 0..budget {
                let Some(callback) = timeout_list.peek() else {
                    break;
                };
                if !callback.is_cancelled() && callback.expired_time > current_time {
                    break;
                }
                if callback.is_cancelled() {
                    cancelled += 1;
                }
                // Retired entries also consume the budget and are freed outside the heap lock.
                callbacks.push(timeout_list.pop().unwrap());
            }
            let has_pending = timeout_list.peek().is_some_and(|callback| {
                callback.is_cancelled() || callback.expired_time <= current_time
            });
            crate::runtime_timeline::timer_collected(
                diagnostic_start,
                before - timeout_list.len(),
                callbacks.len() - cancelled,
                self as *const Self as u64,
            );
            (callbacks, has_pending)
        };

        let diagnostic_start = crate::runtime_timeline::timer_operation_start();
        for callback in callbacks {
            if !callback.is_cancelled() {
                callback.call();
            }
        }
        crate::runtime_timeline::timer_batch_done(diagnostic_start, self as *const Self as u64);
        has_pending
    }

    /// Creates an [`Timer`], which will be managed by this `TimerManager`.
    pub(crate) fn create_timer<F>(self: &Arc<Self>, function: F) -> Arc<Timer>
    where
        F: Fn(TimerGuard) + Send + Sync + 'static,
    {
        Timer::new(function, self.clone())
    }
}

/// A `TimerCallback` can be used to execute a timer callback function.
struct TimerCallback {
    expired_time: Duration,
    timer: Weak<Timer>,
    is_cancelled: AtomicBool,
}

impl TimerCallback {
    /// Creates an instance of `TimerCallback`.
    fn new(timeout: Duration, timer: Weak<Timer>) -> Self {
        Self {
            expired_time: timeout,
            timer,
            is_cancelled: AtomicBool::new(false),
        }
    }

    /// Cancels a `TimerCallback`. If the callback function has not been called,
    /// it will never be called again.
    fn cancel(&self) {
        self.is_cancelled.store(true, Ordering::Release);
    }

    // Returns whether the `TimerCallback` is cancelled.
    fn is_cancelled(&self) -> bool {
        self.is_cancelled.load(Ordering::Acquire)
    }

    fn call(&self) {
        let Some(timer) = self.timer.upgrade() else {
            crate::runtime_timeline::timer_callback(false);
            return;
        };
        crate::runtime_timeline::timer_callback(true);

        let mut timer_guard = timer.lock();

        if self.is_cancelled() {
            // The callback is cancelled.
            return;
        }

        let interval = timer_guard.interval();
        if interval != Duration::ZERO {
            timer_guard.set_timeout(Timeout::After(interval));
        }

        // Pass the `timer_guard` guard to the callback, allowing it to prevent race conditions.
        // The callback may choose to use the guard or drop it if not needed.
        let _timeline_scope = crate::runtime_timeline::timing_enabled().then(|| {
            crate::runtime_timeline::TimerScope::new(
                Arc::ptr_eq(
                    &timer.timer_manager,
                    crate::time::clocks::MonotonicClock::timer_manager(),
                ),
                self.expired_time,
            )
        });
        (timer.registered_callback)(timer_guard);
    }
}

impl PartialEq for TimerCallback {
    fn eq(&self, other: &Self) -> bool {
        self.expired_time == other.expired_time
    }
}

impl Eq for TimerCallback {}

impl PartialOrd for TimerCallback {
    fn partial_cmp(&self, other: &Self) -> Option<core::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for TimerCallback {
    fn cmp(&self, other: &Self) -> core::cmp::Ordering {
        // We want `TimerCallback`s to be processed in ascending order of `expired_time`,
        // and the in-order management of `TimerCallback`s currently relies on a maximum heap,
        // so we need the reverse instruction here.
        self.expired_time.cmp(&other.expired_time).reverse()
    }
}

#[cfg(ktest)]
mod tests {
    use core::sync::atomic::{AtomicU64, AtomicUsize};

    use ostd::prelude::ktest;

    use super::*;

    struct TestClock(AtomicU64);

    impl Clock for TestClock {
        fn read_time(&self) -> Duration {
            Duration::from_nanos(self.0.load(Ordering::Relaxed))
        }
    }

    #[ktest]
    fn bounded_batches_preserve_deadlines_and_order() {
        const BUDGET: usize = 4;
        const EXPIRED: usize = 2 * BUDGET + 3;
        let clock = Arc::new(TestClock(AtomicU64::new(0)));
        let manager = TimerManager::new(clock.clone());
        let observed = Arc::new(SpinLock::<Vec<usize>>::new(Vec::new()));
        let mut timers = Vec::new();
        for deadline in (1..=EXPIRED + 1).rev() {
            let observed = observed.clone();
            let timer = manager.create_timer(move |_| observed.lock().push(deadline));
            timer
                .lock()
                .set_timeout(Timeout::When(Duration::from_nanos(deadline as u64)));
            timers.push(timer);
        }
        assert!(!manager.process_expired_timers_with_budget(BUDGET));
        assert!(observed.lock().is_empty());
        clock.0.store(EXPIRED as u64, Ordering::Relaxed);
        assert!(manager.process_expired_timers_with_budget(BUDGET));
        assert_eq!(observed.lock().len(), BUDGET);
        assert!(manager.process_expired_timers_with_budget(BUDGET));
        assert_eq!(observed.lock().len(), 2 * BUDGET);
        assert!(!manager.process_expired_timers_with_budget(BUDGET));
        assert_eq!(*observed.lock(), (1..=EXPIRED).collect::<Vec<_>>());
        clock.0.store((EXPIRED + 1) as u64, Ordering::Relaxed);
        assert!(!manager.process_expired_timers_with_budget(BUDGET));
        assert_eq!(*observed.lock(), (1..=EXPIRED + 1).collect::<Vec<_>>());
    }

    fn check_inactive_prefix(prepare: impl Fn(&Arc<Timer>)) {
        const BUDGET: usize = 4;
        const INACTIVE: usize = 2 * BUDGET + 1;
        let clock = Arc::new(TestClock(AtomicU64::new((INACTIVE + 1) as u64)));
        let manager = TimerManager::new(clock);
        for deadline in 1..=INACTIVE {
            let timer = manager.create_timer(|_| panic!("inactive timer callback ran"));
            timer
                .lock()
                .set_timeout(Timeout::When(Duration::from_nanos(deadline as u64)));
            prepare(&timer);
        }
        let fired = Arc::new(AtomicUsize::new(0));
        let fired_in_callback = fired.clone();
        let timer = manager.create_timer(move |_| {
            fired_in_callback.fetch_add(1, Ordering::Relaxed);
        });
        timer
            .lock()
            .set_timeout(Timeout::When(Duration::from_nanos((INACTIVE + 1) as u64)));
        assert!(manager.process_expired_timers_with_budget(BUDGET));
        assert_eq!(fired.load(Ordering::Relaxed), 0);
        assert!(manager.process_expired_timers_with_budget(BUDGET));
        assert_eq!(fired.load(Ordering::Relaxed), 0);
        assert!(!manager.process_expired_timers_with_budget(BUDGET));
        assert_eq!(fired.load(Ordering::Relaxed), 1);
        assert!(!manager.process_expired_timers_with_budget(BUDGET));
        assert_eq!(fired.load(Ordering::Relaxed), 1);
    }

    #[ktest]
    fn cancelled_entries_consume_the_batch_budget() {
        check_inactive_prefix(|timer| timer.lock().cancel());
    }

    #[ktest]
    fn dropped_entries_consume_the_batch_budget() {
        check_inactive_prefix(|_| {});
    }
}
