// SPDX-License-Identifier: MPL-2.0

use alloc::{
    boxed::Box,
    sync::{Arc, Weak},
    vec::Vec,
};
use core::{
    sync::atomic::{AtomicBool, AtomicUsize, Ordering},
    time::Duration,
};

use ostd::sync::{LocalIrqDisabled, SpinLock, SpinLockGuard};

use self::queue::TimerQueue;
use super::Clock;

mod queue;

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

        self.cancel();

        self.inner.timer_callback = Arc::downgrade(&new_timer_callback);
        self.timer.timer_manager.insert(new_timer_callback);
    }

    /// Cancels the currently set `TimerCallback`.
    ///
    /// Once cancelled, the current `TimerCallback` will not be triggered again.
    pub(crate) fn cancel(&self) {
        if let Some(timer_callback) = self.inner.timer_callback.upgrade() {
            self.timer.timer_manager.retire(&timer_callback);
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

impl Drop for Timer {
    fn drop(&mut self) {
        if let Some(timer_callback) = self.inner.get_mut().timer_callback.upgrade() {
            self.timer_manager.retire(&timer_callback);
        }
    }
}

/// `TimerManager` is used to create timers and manage their expiries. It holds a clock and can
/// create [`Timer`]s based on this clock.
///
/// These created `Timer`s will hold an `Arc` pointer to this manager, hence this manager
/// will be actually dropped after all the created timers have been dropped.
pub(crate) struct TimerManager {
    clock: Arc<dyn Clock>,
    timer_callbacks: SpinLock<TimerQueue>,
}

impl TimerManager {
    /// Creates a `TimerManager` instance from a clock.
    pub(crate) fn new(clock: Arc<dyn Clock>) -> Arc<Self> {
        Arc::new(Self {
            clock,
            timer_callbacks: SpinLock::new(TimerQueue::new()),
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
        self.timer_callbacks
            .disable_irq()
            .lock()
            .insert(timer_callback);
    }

    fn retire(&self, timer_callback: &Arc<TimerCallback>) {
        // Preserve cancellation for callbacks already selected by a dispatcher.
        timer_callback.cancel();
        let retired = self
            .timer_callbacks
            .disable_irq()
            .lock()
            .remove(timer_callback);
        // Release the callback after the queue guard, including when its owner is being dropped.
        drop(retired);
    }

    /// Checks and processes the managed timers.
    ///
    /// If any of the timers have timed out, call the corresponding callback functions.
    pub(crate) fn process_expired_timers(&self) {
        let callbacks = {
            let mut timeout_list = self.timer_callbacks.disable_irq().lock();
            if timeout_list.is_empty() {
                return;
            }

            let mut callbacks = Vec::new();
            let current_time = self.clock.read_time();
            while let Some(timer_callback) = timeout_list.first() {
                if timer_callback.is_cancelled() {
                    // Just ignore the cancelled callback
                    timeout_list.pop();
                } else if timer_callback.expired_time <= current_time {
                    callbacks.push(timeout_list.pop().unwrap());
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
    pub(crate) fn create_timer<F>(self: &Arc<Self>, function: F) -> Arc<Timer>
    where
        F: Fn(TimerGuard) + Send + Sync + 'static,
    {
        Timer::new(function, self.clone())
    }
}

type TimerCallbackKey = (Duration, usize);

/// A `TimerCallback` can be used to execute a timer callback function.
struct TimerCallback {
    expired_time: Duration,
    timer: Weak<Timer>,
    is_cancelled: AtomicBool,
    // Only the owning manager's queue lock permits access to this position.
    queue_position: AtomicUsize,
}

impl TimerCallback {
    /// Creates an instance of `TimerCallback`.
    fn new(timeout: Duration, timer: Weak<Timer>) -> Self {
        Self {
            expired_time: timeout,
            timer,
            is_cancelled: AtomicBool::new(false),
            queue_position: AtomicUsize::new(queue::NOT_QUEUED),
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

    fn key(self: &Arc<Self>) -> TimerCallbackKey {
        // The queued Arc keeps the allocation alive, so its address distinguishes equal deadlines.
        (self.expired_time, Arc::as_ptr(self) as usize)
    }

    fn call(&self) {
        let Some(timer) = self.timer.upgrade() else {
            return;
        };

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
        (timer.registered_callback)(timer_guard);
    }
}

#[cfg(ktest)]
mod tests {
    use alloc::vec;
    use core::sync::atomic::AtomicU64;

    use ostd::prelude::ktest;

    use super::*;

    struct TestClock(AtomicU64);

    impl Clock for TestClock {
        fn read_time(&self) -> Duration {
            Duration::from_nanos(self.0.load(Ordering::Relaxed))
        }
    }

    fn pending(manager: &TimerManager) -> usize {
        manager.timer_callbacks.disable_irq().lock().len()
    }

    fn selected_callback(manager: &TimerManager) -> Arc<TimerCallback> {
        manager.timer_callbacks.disable_irq().lock().pop().unwrap()
    }

    fn dropping_owners_retires_queued_entries() {
        let clock = Arc::new(TestClock(AtomicU64::new(0)));
        let manager = TimerManager::new(clock);
        for deadline in [Duration::ZERO, Duration::from_secs(3600)] {
            for _ in 0..1024 {
                let timer = manager.create_timer(|_| panic!("dropped timer fired"));
                timer.lock().set_timeout(Timeout::When(deadline));
            }
            assert_eq!(pending(&manager), 0, "dropped owners left queued callbacks");
        }
        let active = manager.create_timer(|_| {});
        active
            .lock()
            .set_timeout(Timeout::After(Duration::from_secs(7200)));
        for _ in 0..1024 {
            let temporary = manager.create_timer(|_| {});
            temporary
                .lock()
                .set_timeout(Timeout::After(Duration::from_secs(3600)));
        }
        assert_eq!(pending(&manager), 1, "queue retained historical owners");
        drop(active);
        assert_eq!(pending(&manager), 0);
    }

    fn cancelling_and_rearming_retire_old_entries() {
        let clock = Arc::new(TestClock(AtomicU64::new(0)));
        let manager = TimerManager::new(clock.clone());
        let fired = Arc::new(AtomicUsize::new(0));
        let observed = fired.clone();
        let timer = manager.create_timer(move |_| {
            observed.fetch_add(1, Ordering::Relaxed);
        });
        for deadline in 1..=1024 {
            timer
                .lock()
                .set_timeout(Timeout::When(Duration::from_nanos(deadline)));
            assert_eq!(pending(&manager), 1, "rearm retained the old callback");
        }
        clock.0.store(1023, Ordering::Relaxed);
        manager.process_expired_timers();
        assert_eq!(fired.load(Ordering::Relaxed), 0);
        clock.0.store(1024, Ordering::Relaxed);
        manager.process_expired_timers();
        assert_eq!(fired.load(Ordering::Relaxed), 1);
        for _ in 0..1024 {
            timer
                .lock()
                .set_timeout(Timeout::After(Duration::from_secs(3600)));
            timer.lock().cancel();
            assert_eq!(pending(&manager), 0, "cancel retained a queued callback");
            assert_eq!(timer.lock().remain(), Duration::ZERO);
        }
    }

    fn cancellation_after_selection_prevents_execution() {
        let clock = Arc::new(TestClock(AtomicU64::new(0)));
        let manager = TimerManager::new(clock);
        let timer = manager.create_timer(|_| panic!("selected cancelled callback fired"));
        timer.lock().set_timeout(Timeout::When(Duration::ZERO));
        let callback = selected_callback(&manager);
        timer.lock().cancel();
        callback.call();
        assert_eq!(pending(&manager), 0);

        timer.lock().set_timeout(Timeout::When(Duration::ZERO));
        let callback = selected_callback(&manager);
        drop(timer);
        callback.call();
        assert_eq!(pending(&manager), 0);
    }

    fn equal_deadlines_keep_distinct_callbacks() {
        let clock = Arc::new(TestClock(AtomicU64::new(0)));
        let manager = TimerManager::new(clock.clone());
        let observed = Arc::new(SpinLock::<Vec<usize>>::new(Vec::new()));
        let mut timers = Vec::new();
        for index in 0..8 {
            let observed = observed.clone();
            let timer = manager.create_timer(move |_| observed.lock().push(index));
            timer
                .lock()
                .set_timeout(Timeout::When(Duration::from_nanos(10)));
            timers.push(timer);
        }
        timers[3].lock().cancel();
        assert_eq!(pending(&manager), 7);
        clock.0.store(9, Ordering::Relaxed);
        manager.process_expired_timers();
        assert!(observed.lock().is_empty());
        clock.0.store(10, Ordering::Relaxed);
        manager.process_expired_timers();
        observed.lock().sort_unstable();
        assert_eq!(*observed.lock(), vec![0, 1, 2, 4, 5, 6, 7]);
        assert_eq!(pending(&manager), 0);
        manager.process_expired_timers();
        assert_eq!(observed.lock().len(), 7);
    }

    fn periodic_callbacks_survive_rearm_and_stop_on_retirement() {
        let clock = Arc::new(TestClock(AtomicU64::new(0)));
        let manager = TimerManager::new(clock.clone());
        let fired = Arc::new(AtomicUsize::new(0));
        let observed = fired.clone();
        let timer = manager.create_timer(move |_| {
            observed.fetch_add(1, Ordering::Relaxed);
        });
        timer.lock().set_interval(Duration::from_nanos(10));
        timer
            .lock()
            .set_timeout(Timeout::When(Duration::from_nanos(10)));
        for now in [10, 20] {
            clock.0.store(now, Ordering::Relaxed);
            manager.process_expired_timers();
            assert_eq!(pending(&manager), 1);
        }
        assert_eq!(fired.load(Ordering::Relaxed), 2);
        drop(timer);
        assert_eq!(pending(&manager), 0);
        clock.0.store(30, Ordering::Relaxed);
        manager.process_expired_timers();
        assert_eq!(fired.load(Ordering::Relaxed), 2);
    }

    fn periodic_callback_can_cancel_its_new_timeout() {
        let clock = Arc::new(TestClock(AtomicU64::new(0)));
        let manager = TimerManager::new(clock.clone());
        let fired = Arc::new(AtomicUsize::new(0));
        let observed = fired.clone();
        let timer = manager.create_timer(move |guard| {
            observed.fetch_add(1, Ordering::Relaxed);
            guard.cancel();
        });
        timer.lock().set_interval(Duration::from_nanos(10));
        timer
            .lock()
            .set_timeout(Timeout::When(Duration::from_nanos(10)));
        clock.0.store(10, Ordering::Relaxed);
        manager.process_expired_timers();
        assert_eq!(fired.load(Ordering::Relaxed), 1);
        assert_eq!(pending(&manager), 0);
        clock.0.store(20, Ordering::Relaxed);
        manager.process_expired_timers();
        assert_eq!(fired.load(Ordering::Relaxed), 1);
    }

    #[ktest]
    fn timer_lifecycle_regressions() {
        dropping_owners_retires_queued_entries();
        cancelling_and_rearming_retire_old_entries();
        cancellation_after_selection_prevents_execution();
        equal_deadlines_keep_distinct_callbacks();
        periodic_callbacks_survive_rearm_and_stop_on_retirement();
        periodic_callback_can_cancel_its_new_timeout();
    }

    #[ktest]
    fn selected_old_generation_cannot_replace_a_same_deadline_rearm() {
        let clock = Arc::new(TestClock(AtomicU64::new(0)));
        let manager = TimerManager::new(clock);
        let fired = Arc::new(AtomicUsize::new(0));
        let observed = fired.clone();
        let timer = manager.create_timer(move |guard| {
            observed.fetch_add(1, Ordering::Relaxed);
            guard.cancel();
        });
        timer.lock().set_interval(Duration::from_nanos(10));
        timer.lock().set_timeout(Timeout::When(Duration::ZERO));
        let old = selected_callback(&manager);
        timer.lock().set_timeout(Timeout::When(Duration::ZERO));
        old.call();
        assert_eq!(fired.load(Ordering::Relaxed), 0);
        assert_eq!(pending(&manager), 1);
        manager.process_expired_timers();
        assert_eq!(fired.load(Ordering::Relaxed), 1);
        assert_eq!(pending(&manager), 0);
    }

    #[ktest]
    fn cancelled_future_head_is_removed_before_its_deadline() {
        let clock = Arc::new(TestClock(AtomicU64::new(0)));
        let manager = TimerManager::new(clock.clone());
        let cancelled = manager.create_timer(|_| panic!("cancelled future callback fired"));
        cancelled
            .lock()
            .set_timeout(Timeout::When(Duration::from_nanos(10)));
        let fired = Arc::new(AtomicUsize::new(0));
        let observed = fired.clone();
        let future = manager.create_timer(move |_| {
            observed.fetch_add(1, Ordering::Relaxed);
        });
        future
            .lock()
            .set_timeout(Timeout::When(Duration::from_nanos(20)));
        let old = cancelled.lock().inner.timer_callback.upgrade().unwrap();
        // Model dispatch between retirement's cancellation publication and removal.
        old.cancel();
        manager.process_expired_timers();
        assert_eq!(pending(&manager), 1);
        manager.retire(&old);
        assert_eq!(pending(&manager), 1);
        clock.0.store(20, Ordering::Relaxed);
        manager.process_expired_timers();
        assert_eq!(fired.load(Ordering::Relaxed), 1);
        assert_eq!(pending(&manager), 0);
    }

    struct ConcurrentTimers {
        manager: Arc<TimerManager>,
        observations: SpinLock<Vec<Arc<AtomicUsize>>>,
        completed: Vec<AtomicUsize>,
        iterations_per_round: usize,
    }

    static CONCURRENT_TIMERS: SpinLock<Option<Arc<ConcurrentTimers>>> = SpinLock::new(None);

    fn exercise_concurrent_timers() {
        use ostd::cpu::PinCurrentCpu;

        let state = CONCURRENT_TIMERS
            .disable_irq()
            .lock()
            .as_ref()
            .unwrap()
            .clone();
        for iteration in 0..state.iterations_per_round {
            let fired = Arc::new(AtomicUsize::new(0));
            state.observations.disable_irq().lock().push(fired.clone());
            let timer = state.manager.create_timer(move |_| {
                fired.fetch_add(1, Ordering::Relaxed);
            });
            {
                let mut guard = timer.lock();
                guard.set_timeout(Timeout::When(Duration::ZERO));
                guard.set_timeout(Timeout::When(Duration::ZERO));
            }
            if iteration % 3 == 0 {
                timer.lock().cancel();
            } else {
                state.manager.process_expired_timers();
            }
            drop(timer);
        }
        let cpu = ostd::irq::disable_local().current_cpu();
        state.completed[u32::from(cpu) as usize]
            .fetch_add(state.iterations_per_round, Ordering::Release);
    }

    #[ktest]
    fn concurrent_rearm_cancel_and_dispatch_preserve_generations() {
        check_concurrent_timers(16, 32, 512);
    }

    #[ktest]
    fn shared_timer_queue_survives_smp_churn() {
        check_concurrent_timers(125, 200, 25000);
    }

    fn check_concurrent_timers(
        iterations_per_round: usize,
        rounds: usize,
        expected_per_cpu: usize,
    ) {
        let clock = Arc::new(TestClock(AtomicU64::new(0)));
        let manager = TimerManager::new(clock);
        let mut sentinels = Vec::new();
        for _ in 0..64 {
            let timer = manager.create_timer(|_| panic!("future timer fired"));
            timer
                .lock()
                .set_timeout(Timeout::When(Duration::from_secs(1)));
            sentinels.push(timer);
        }
        let state = Arc::new(ConcurrentTimers {
            manager: manager.clone(),
            observations: SpinLock::new(Vec::new()),
            completed: (0..ostd::cpu::num_cpus())
                .map(|_| AtomicUsize::new(0))
                .collect(),
            iterations_per_round,
        });
        *CONCURRENT_TIMERS.disable_irq().lock() = Some(state.clone());
        for _ in 0..rounds {
            ostd::smp::inter_processor_call(
                &ostd::cpu::CpuSet::new_full(),
                exercise_concurrent_timers,
            )
            .wait();
        }
        CONCURRENT_TIMERS.disable_irq().lock().take();
        manager.process_expired_timers();
        for count in &state.completed {
            assert_eq!(count.load(Ordering::Acquire), expected_per_cpu);
        }
        let observations = state.observations.disable_irq().lock();
        assert_eq!(observations.len(), ostd::cpu::num_cpus() * expected_per_cpu);
        assert!(
            observations
                .iter()
                .all(|count| count.load(Ordering::Relaxed) <= 1)
        );
        assert!(
            observations
                .iter()
                .any(|count| count.load(Ordering::Relaxed) == 1)
        );
        assert_eq!(pending(&manager), sentinels.len());
        drop(sentinels);
        assert_eq!(pending(&manager), 0);
    }
}
