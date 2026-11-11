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
