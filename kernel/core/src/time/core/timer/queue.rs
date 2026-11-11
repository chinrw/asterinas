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

