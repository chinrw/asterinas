// SPDX-License-Identifier: MPL-2.0

//! Orders timer callbacks and allows their owning manager to retire them.
//! The manager serializes queue and callback position access with its queue lock.

use alloc::{sync::Arc, vec::Vec};
use core::sync::atomic::Ordering;

use super::TimerCallback;

pub(super) const NOT_QUEUED: usize = usize::MAX;

/// A timer queue whose entries can be removed through their callback handles.
pub(super) struct TimerQueue {
    callbacks: Vec<Arc<TimerCallback>>,
}

impl TimerQueue {
    pub(super) fn new() -> Self {
        Self {
            callbacks: Vec::new(),
        }
    }

    pub(super) fn is_empty(&self) -> bool {
        self.callbacks.is_empty()
    }

    #[cfg(ktest)]
    pub(super) fn len(&self) -> usize {
        self.callbacks.len()
    }

    pub(super) fn first(&self) -> Option<&Arc<TimerCallback>> {
        self.callbacks.first()
    }

    pub(super) fn insert(&mut self, callback: Arc<TimerCallback>) {
        debug_assert_eq!(callback.queue_position.load(Ordering::Relaxed), NOT_QUEUED);
        let position = self.callbacks.len();
        self.callbacks.push(callback);
        self.callbacks[position]
            .queue_position
            .store(position, Ordering::Relaxed);
        self.sift_up(position);
    }

    pub(super) fn remove(&mut self, callback: &Arc<TimerCallback>) -> Option<Arc<TimerCallback>> {
        // Positions and slot contents are accessed under the manager's queue lock.
        // Relaxed atomics provide interior mutability, not a lock-free membership test.
        let position = callback.queue_position.load(Ordering::Relaxed);
        if position == NOT_QUEUED {
            return None;
        }
        debug_assert!(Arc::ptr_eq(&self.callbacks[position], callback));
        Some(self.remove_at(position))
    }

    pub(super) fn pop(&mut self) -> Option<Arc<TimerCallback>> {
        if self.callbacks.is_empty() {
            None
        } else {
            Some(self.remove_at(0))
        }
    }

    fn remove_at(&mut self, position: usize) -> Arc<TimerCallback> {
        let removed = self.callbacks.swap_remove(position);
        removed.queue_position.store(NOT_QUEUED, Ordering::Relaxed);
        if position < self.callbacks.len() {
            self.callbacks[position]
                .queue_position
                .store(position, Ordering::Relaxed);
            if position > 0
                && self.callbacks[position].key() < self.callbacks[(position - 1) / 2].key()
            {
                self.sift_up(position);
            } else {
                self.sift_down(position);
            }
        }
        removed
    }

    fn sift_up(&mut self, mut position: usize) {
        while position > 0 {
            let parent = (position - 1) / 2;
            if self.callbacks[parent].key() <= self.callbacks[position].key() {
                break;
            }
            self.swap(parent, position);
            position = parent;
        }
    }

    fn sift_down(&mut self, mut position: usize) {
        let len = self.callbacks.len();
        while position < len / 2 {
            let mut child = position * 2 + 1;
            if child + 1 < len && self.callbacks[child + 1].key() < self.callbacks[child].key() {
                child += 1;
            }
            if self.callbacks[position].key() <= self.callbacks[child].key() {
                break;
            }
            self.swap(position, child);
            position = child;
        }
    }

    fn swap(&mut self, first: usize, second: usize) {
        self.callbacks.swap(first, second);
        self.callbacks[first]
            .queue_position
            .store(first, Ordering::Relaxed);
        self.callbacks[second]
            .queue_position
            .store(second, Ordering::Relaxed);
    }
}

impl Drop for TimerQueue {
    fn drop(&mut self) {
        for callback in &self.callbacks {
            callback.queue_position.store(NOT_QUEUED, Ordering::Relaxed);
        }
    }
}

#[cfg(ktest)]
mod tests {
    use alloc::{collections::BTreeMap, sync::Weak, vec};
    use core::time::Duration;

    use ostd::prelude::ktest;

    use super::*;

    fn callback(deadline: u64) -> Arc<TimerCallback> {
        Arc::new(TimerCallback::new(
            Duration::from_nanos(deadline),
            Weak::new(),
        ))
    }

    fn check_positions(queue: &TimerQueue) {
        for (position, callback) in queue.callbacks.iter().enumerate() {
            assert_eq!(callback.queue_position.load(Ordering::Relaxed), position);
            if position > 0 {
                assert!(queue.callbacks[(position - 1) / 2].key() <= callback.key());
            }
        }
    }

    #[ktest]
    fn arbitrary_removal_preserves_expiry_order() {
        // The last entry must rise after removing 11, and sink after removing 2.
        for (deadlines, removed) in [([1, 10, 2, 11, 12, 3, 4], 3), ([1, 2, 3, 4, 5, 6, 7], 1)] {
            let mut queue = TimerQueue::new();
            let handles: Vec<_> = deadlines.into_iter().map(callback).collect();
            for handle in &handles {
                queue.insert(handle.clone());
            }
            queue.remove(&handles[removed]).unwrap();
            assert_eq!(
                handles[removed].queue_position.load(Ordering::Relaxed),
                NOT_QUEUED
            );
            check_positions(&queue);
            let mut expected: Vec<_> = deadlines.into_iter().collect();
            expected.remove(removed);
            expected.sort_unstable();
            for deadline in expected {
                assert_eq!(
                    queue.pop().unwrap().expired_time,
                    Duration::from_nanos(deadline)
                );
                check_positions(&queue);
            }
            assert!(queue.pop().is_none());
        }
    }

    #[ktest]
    fn stale_handles_cannot_remove_reused_slots() {
        let mut queue = TimerQueue::new();
        let old = callback(1);
        queue.insert(old.clone());
        assert!(Arc::ptr_eq(&queue.pop().unwrap(), &old));
        let new = callback(1);
        queue.insert(new.clone());
        assert!(queue.remove(&old).is_none());
        assert!(Arc::ptr_eq(queue.first().unwrap(), &new));
        queue.remove(&new).unwrap();
        queue.insert(callback(2));
        assert!(queue.remove(&new).is_none());
        assert_eq!(old.queue_position.load(Ordering::Relaxed), NOT_QUEUED);
    }

    #[ktest]
    fn growth_and_removal_match_an_ordered_reference() {
        let mut queue = TimerQueue::new();
        let mut reference = BTreeMap::new();
        let mut handles: Vec<Option<Arc<TimerCallback>>> = vec![None; 128];
        let mut random = 1u64;
        for _ in 0..10_000 {
            random = random.wrapping_mul(6364136223846793005).wrapping_add(1);
            let slot = (random >> 32) as usize % handles.len();
            if let Some(old) = handles[slot].take() {
                let expected = reference.remove(&old.key());
                assert_eq!(queue.remove(&old).is_some(), expected.is_some());
                assert!(queue.remove(&old).is_none());
            }
            let next = callback(random % 16);
            queue.insert(next.clone());
            reference.insert(next.key(), next.clone());
            handles[slot] = Some(next);
            if random & 3 == 0 {
                assert_eq!(
                    queue.pop().map(|entry| entry.key()),
                    reference.pop_first().map(|entry| entry.0)
                );
            }
            assert_eq!(queue.callbacks.len(), reference.len());
            assert_eq!(
                queue.first().map(|entry| entry.key()),
                reference.first_key_value().map(|entry| *entry.0)
            );
            check_positions(&queue);
        }
        while queue.pop().is_some() {}
        for handle in handles.into_iter().flatten() {
            assert_eq!(handle.queue_position.load(Ordering::Relaxed), NOT_QUEUED);
        }
        let initial_capacity = queue.callbacks.capacity();
        let count = initial_capacity + 4097;
        for deadline in (0..count).rev() {
            queue.insert(callback(deadline as u64));
        }
        assert!(queue.callbacks.capacity() > initial_capacity);
        check_positions(&queue);
        for deadline in 0..count {
            assert_eq!(
                queue.pop().unwrap().expired_time,
                Duration::from_nanos(deadline as u64)
            );
        }
        let retained = callback(1);
        queue.insert(retained.clone());
        drop(queue);
        assert_eq!(retained.queue_position.load(Ordering::Relaxed), NOT_QUEUED);
    }
}
