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
