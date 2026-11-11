// SPDX-License-Identifier: MPL-2.0

//! Timer queue types backed by the owner-checked OSTD collection.

use super::TimerCallbackData;

pub(super) type TimerQueue = timer_queue::TimerQueue<TimerCallbackData>;
pub(super) type TimerFactory = timer_queue::Factory<TimerCallbackData>;

#[cfg(ktest)]
mod tests {
    use alloc::sync::Weak;
    use core::time::Duration;

    use ostd::prelude::ktest;

    use super::{super::TimerCallback, *};

    fn callback(
        factory: &TimerFactory,
        deadline: u64,
    ) -> (timer_queue::NewNode<TimerCallbackData>, TimerCallback) {
        let node = factory.create(
            Duration::from_nanos(deadline),
            TimerCallbackData::new(Weak::new()),
        );
        let handle = node.downgrade().upgrade().unwrap();
        (node, handle)
    }

    #[ktest]
    fn cached_intrusive_head_tracks_removal_and_pop() {
        let (mut queue, factory) = TimerQueue::new();
        for deadline in [9, 1, 3, 1, 5] {
            assert!(queue.insert(callback(&factory, deadline).0).is_ok());
        }
        assert_eq!(queue.count(), 5);
        assert_eq!(queue.first().unwrap().deadline(), Duration::from_nanos(1));
        let first = queue.first().unwrap().clone();
        assert!(queue.remove(&first).0.unwrap().ptr_eq(&first));
        assert!(queue.remove(&first).0.is_none());
        assert_eq!(queue.first().unwrap().deadline(), Duration::from_nanos(1));
        for expected in [1, 3, 5, 9] {
            assert_eq!(
                queue.pop_first().unwrap().deadline(),
                Duration::from_nanos(expected)
            );
        }
        assert!(queue.first().is_none());
        assert!(queue.pop_first().is_none());
    }

    #[ktest]
    fn foreign_and_unlinked_handles_preserve_intrusive_membership() {
        let (mut first, first_factory) = TimerQueue::new();
        let (mut second, second_factory) = TimerQueue::new();
        let (node, stale) = callback(&first_factory, 10);
        assert!(first.insert(node).is_ok());
        assert!(first.pop_first().unwrap().ptr_eq(&stale));
        assert!(first.insert(callback(&first_factory, 10).0).is_ok());
        assert!(first.remove(&stale).0.is_none());
        let (node, live) = callback(&first_factory, 10);
        assert!(first.insert(node).is_ok());
        assert!(second.insert(callback(&second_factory, 1).0).is_ok());
        assert!(second.remove(&live).0.is_none());
        assert_eq!(first.count(), 2);
        assert_eq!(second.count(), 1);
        drop(first);
        assert!(second.remove(&live).0.is_none());
        assert_eq!(second.first().unwrap().deadline(), Duration::from_nanos(1));
    }

    #[ktest]
    fn intrusive_cache_references_end_with_the_queue() {
        let (mut queue, factory) = TimerQueue::new();
        let (node, handle) = callback(&factory, 10);
        let weak = handle.downgrade();
        assert!(queue.insert(node).is_ok());
        drop(handle);
        assert!(weak.upgrade().is_some());
        drop(queue);
        assert!(weak.upgrade().is_none());
    }
}
