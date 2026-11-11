// SPDX-License-Identifier: MPL-2.0

//! Keeps the earliest queued callback available without traversing the tree.

use alloc::{collections::BTreeMap, sync::Arc};

use super::{TimerCallback, TimerCallbackKey};

pub(super) struct TimerQueue {
    callbacks: BTreeMap<TimerCallbackKey, Arc<TimerCallback>>,
    earliest: Option<Arc<TimerCallback>>,
}

impl TimerQueue {
    pub(super) fn new() -> Self {
        Self {
            callbacks: BTreeMap::new(),
            earliest: None,
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
        self.earliest.as_ref()
    }

    pub(super) fn insert(&mut self, callback: Arc<TimerCallback>) {
        let key = callback.key();
        let new_earliest = self
            .earliest
            .as_ref()
            .is_none_or(|earliest| key < earliest.key())
            .then(|| callback.clone());
        let replaced = self.callbacks.insert(key, callback);
        if let Some(earliest) = new_earliest {
            self.earliest = Some(earliest);
        }
        debug_assert!(replaced.is_none());
    }

    pub(super) fn remove(
        &mut self,
        callback: &Arc<TimerCallback>,
    ) -> (Option<Arc<TimerCallback>>, Option<Arc<TimerCallback>>) {
        let removed = self.callbacks.remove(&callback.key());
        let previous_earliest = if removed.as_ref().is_some_and(|removed| {
            self.earliest
                .as_ref()
                .is_some_and(|earliest| Arc::ptr_eq(removed, earliest))
        }) {
            self.refresh_earliest()
        } else {
            None
        };
        // Let explicit retirement release both references after the queue unlocks.
        (removed, previous_earliest)
    }

    pub(super) fn pop_first(&mut self) -> Option<Arc<TimerCallback>> {
        let (_, callback) = self.callbacks.pop_first()?;
        // The local Arc keeps this generation alive while its cache reference drops.
        drop(self.refresh_earliest());
        Some(callback)
    }

    fn refresh_earliest(&mut self) -> Option<Arc<TimerCallback>> {
        let earliest = self
            .callbacks
            .first_key_value()
            .map(|(_, callback)| callback.clone());
        core::mem::replace(&mut self.earliest, earliest)
    }
}

#[cfg(ktest)]
mod tests {
    use alloc::{sync::Weak, vec::Vec};
    use core::time::Duration;

    use ostd::prelude::ktest;

    use super::*;

    fn callback(deadline: u64) -> Arc<TimerCallback> {
        Arc::new(TimerCallback::new(
            Duration::from_nanos(deadline),
            Weak::new(),
        ))
    }

    fn check_earliest(queue: &TimerQueue) {
        match (queue.callbacks.first_key_value(), queue.first()) {
            (Some((_, expected)), Some(actual)) => assert!(Arc::ptr_eq(expected, actual)),
            (None, None) => {}
            _ => panic!("cached head disagrees with queued generations"),
        }
    }

    #[ktest]
    fn cached_head_tracks_insert_remove_and_pop() {
        let mut queue = TimerQueue::new();
        check_earliest(&queue);
        assert!(queue.pop_first().is_none());
        let handles: Vec<_> = [30, 10, 20, 10].into_iter().map(callback).collect();
        for handle in &handles {
            queue.insert(handle.clone());
            check_earliest(&queue);
        }
        assert!(queue.remove(&callback(5)).0.is_none());
        check_earliest(&queue);
        assert!(queue.remove(&handles[0]).0.is_some());
        check_earliest(&queue);
        assert!(queue.remove(&handles[0]).0.is_none());
        check_earliest(&queue);
        let first = queue.first().unwrap().clone();
        assert!(queue.remove(&first).0.is_some());
        check_earliest(&queue);
        while let Some(expected) = queue.first().cloned() {
            assert!(Arc::ptr_eq(&expected, &queue.pop_first().unwrap()));
            check_earliest(&queue);
        }
        assert!(queue.is_empty());
    }

    #[ktest]
    fn retirement_and_drop_release_cached_references() {
        let mut queue = TimerQueue::new();
        let entry = callback(10);
        let weak = Arc::downgrade(&entry);
        queue.insert(entry.clone());
        let retired = queue.remove(&entry);
        drop(entry);
        assert!(weak.upgrade().is_some());
        drop(retired);
        assert!(weak.upgrade().is_none());
        check_earliest(&queue);

        let entry = callback(20);
        let weak = Arc::downgrade(&entry);
        queue.insert(entry);
        let selected = queue.pop_first().unwrap();
        drop(selected);
        assert!(weak.upgrade().is_none());

        let entry = callback(30);
        let weak = Arc::downgrade(&entry);
        queue.insert(entry);
        drop(queue);
        assert!(weak.upgrade().is_none());
    }
}
