// SPDX-License-Identifier: MPL-2.0

//! A cached timer queue with owner-checked callback handles.

#![no_std]

extern crate alloc;

use alloc::sync::{Arc, Weak};
use core::{marker::PhantomData, ops::Deref, time::Duration};

use intrusive_collections::{KeyAdapter, RBTree, RBTreeAtomicLink, intrusive_adapter};

struct Node<P> {
    link: RBTreeAtomicLink,
    deadline: Duration,
    owner: Arc<()>,
    payload: P,
}

intrusive_adapter!(NodeAdapter<P> = Arc<Node<P>>: Node<P> { link: RBTreeAtomicLink });

impl<'a, P> KeyAdapter<'a> for NodeAdapter<P> {
    type Key = (Duration, usize);

    fn get_key(&self, node: &'a Node<P>) -> Self::Key {
        (node.deadline, node as *const Node<P> as usize)
    }
}

/// A strong reference to one callback generation.
///
/// Handles expose the payload, but cannot change a node's deadline, link or owner.
pub struct Handle<P>(Arc<Node<P>>);

impl<P> Clone for Handle<P> {
    fn clone(&self) -> Self {
        Self(self.0.clone())
    }
}

impl<P> Deref for Handle<P> {
    type Target = P;

    fn deref(&self) -> &P {
        &self.0.payload
    }
}

impl<P> Handle<P> {
    /// Returns this generation's immutable deadline.
    pub fn deadline(&self) -> Duration {
        self.0.deadline
    }

    /// Returns whether two handles refer to the same generation.
    pub fn ptr_eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }

    /// Creates a weak reference to this generation.
    pub fn downgrade(&self) -> WeakHandle<P> {
        WeakHandle(Arc::downgrade(&self.0))
    }

    /// Returns a stable identity for reference-model checks.
    #[cfg(feature = "validation")]
    pub fn identity(&self) -> usize {
        Arc::as_ptr(&self.0) as usize
    }

    /// Returns membership for validation performed under the queue's lock.
    #[cfg(feature = "validation")]
    pub fn is_linked(&self) -> bool {
        self.0.link.is_linked()
    }
}

/// A weak reference to a callback generation, including one not yet queued.
pub struct WeakHandle<P>(Weak<Node<P>>);

impl<P> Default for WeakHandle<P> {
    fn default() -> Self {
        Self(Weak::new())
    }
}

impl<P> Clone for WeakHandle<P> {
    fn clone(&self) -> Self {
        Self(self.0.clone())
    }
}

impl<P> WeakHandle<P> {
    /// Upgrades the reference while this generation still has a strong owner.
    pub fn upgrade(&self) -> Option<Handle<P>> {
        self.0.upgrade().map(Handle)
    }
}

/// A fresh node which can be inserted only once.
pub struct NewNode<P>(Arc<Node<P>>);

impl<P> NewNode<P> {
    /// Creates a weak reference before insertion, allowing timer rearm ordering.
    pub fn downgrade(&self) -> WeakHandle<P> {
        WeakHandle(Arc::downgrade(&self.0))
    }
}

/// A factory for generations belonging to one queue.
pub struct Factory<P> {
    owner: Arc<()>,
    payload: PhantomData<fn() -> P>,
}

impl<P> Clone for Factory<P> {
    fn clone(&self) -> Self {
        Self {
            owner: self.owner.clone(),
            payload: PhantomData,
        }
    }
}

impl<P> Factory<P> {
    /// Allocates a fresh generation with an immutable deadline and queue identity.
    pub fn create(&self, deadline: Duration, payload: P) -> NewNode<P> {
        NewNode(Arc::new(Node {
            link: RBTreeAtomicLink::new(),
            deadline,
            owner: self.owner.clone(),
            payload,
        }))
    }
}

/// A deadline-ordered callback queue with a cached minimum.
///
/// Callers serialize mutations through exclusive access, normally a queue lock.
/// Only this module can link nodes.
/// A fixed, immutable key makes comparison
/// non-panicking after the intrusive collection acquires a node's link.
pub struct TimerQueue<P> {
    nodes: RBTree<NodeAdapter<P>>,
    earliest: Option<Handle<P>>,
    owner: Arc<()>,
}

impl<P> TimerQueue<P> {
    /// Creates a queue and its generation factory with a unique shared identity.
    pub fn new() -> (Self, Factory<P>) {
        let owner = Arc::new(());
        let factory = Factory {
            owner: owner.clone(),
            payload: PhantomData,
        };
        (
            Self {
                nodes: RBTree::new(NodeAdapter::new()),
                earliest: None,
                owner,
            },
            factory,
        )
    }

    /// Returns whether the queue contains no generations.
    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    /// Returns the cached earliest generation.
    pub fn first(&self) -> Option<&Handle<P>> {
        self.earliest.as_ref()
    }

    /// Inserts a fresh generation, returning it unchanged for a foreign queue.
    pub fn insert(&mut self, node: NewNode<P>) -> Result<(), NewNode<P>> {
        if !Arc::ptr_eq(&self.owner, &node.0.owner) {
            return Err(node);
        }
        let first = self
            .earliest
            .as_ref()
            .is_none_or(|head| {
                (node.0.deadline, Arc::as_ptr(&node.0) as usize)
                    < (head.deadline(), Arc::as_ptr(&head.0) as usize)
            })
            .then(|| Handle(node.0.clone()));
        // NewNode is consumed and cannot be reconstructed from a Handle. Its
        // private link is fresh, and key comparison cannot unwind mid-insertion.
        self.nodes.insert(node.0);
        if let Some(first) = first {
            self.earliest = Some(first);
        }
        Ok(())
    }

    /// Removes a still-queued generation from this owner.
    ///
    /// Both returned references may be released after the caller unlocks.
    pub fn remove(&mut self, handle: &Handle<P>) -> (Option<Handle<P>>, Option<Handle<P>>) {
        if !Arc::ptr_eq(&self.owner, &handle.0.owner) || !handle.0.link.is_linked() {
            return (None, None);
        }
        let is_first = self
            .earliest
            .as_ref()
            .is_some_and(|head| head.ptr_eq(handle));
        // SAFETY: Only this queue can insert nodes with this owner. A strong
        // handle retains the owner token, preventing identity reuse. The private
        // link is linked, and exclusive access prevents concurrent removal.
        let mut cursor = unsafe { self.nodes.cursor_mut_from_ptr(Arc::as_ptr(&handle.0)) };
        let removed = cursor.remove().map(Handle);
        let previous = if is_first {
            let next = cursor.as_cursor().clone_pointer().map(Handle);
            core::mem::replace(&mut self.earliest, next)
        } else {
            None
        };
        (removed, previous)
    }

    /// Removes the earliest generation, leaving its final reference to the caller.
    pub fn pop_first(&mut self) -> Option<Handle<P>> {
        let first = self.earliest.as_ref()?;
        // SAFETY: The private cache holds a strong reference to this tree's
        // current minimum. No operation can mutate its link without &mut self.
        let mut cursor = unsafe { self.nodes.cursor_mut_from_ptr(Arc::as_ptr(&first.0)) };
        let removed = cursor.remove().map(Handle);
        let next = cursor.as_cursor().clone_pointer().map(Handle);
        // The removed reference keeps the old cache's allocation alive here.
        self.earliest = next;
        removed
    }

    /// Counts queued generations in O(N), for validation outside hot paths.
    pub fn count(&self) -> usize {
        self.nodes.iter().count()
    }

    /// Returns strong references in key order for reference-model checks.
    #[cfg(feature = "validation")]
    pub fn snapshot(&self) -> alloc::vec::Vec<Handle<P>> {
        let mut cursor = self.nodes.front();
        let mut handles = alloc::vec::Vec::new();
        while let Some(node) = cursor.clone_pointer() {
            handles.push(Handle(node));
            cursor.move_next();
        }
        handles
    }

    /// Returns the node payload layout, excluding its Arc allocation header.
    #[cfg(feature = "validation")]
    pub fn node_layout() -> core::alloc::Layout {
        core::alloc::Layout::new::<Node<P>>()
    }
}

#[cfg(test)]
mod tests {
    extern crate std;

    use std::{
        collections::{BTreeMap, HashSet},
        sync::{
            Mutex,
            atomic::{AtomicBool, AtomicUsize, Ordering::SeqCst},
        },
        thread,
        vec::Vec,
    };

    use super::*;

    fn key<P>(handle: &Handle<P>) -> (Duration, usize) {
        (handle.deadline(), Arc::as_ptr(&handle.0) as usize)
    }

    #[test]
    fn foreign_nodes_and_handles_are_rejected() {
        let (mut first, first_factory) = TimerQueue::new();
        let (mut second, second_factory) = TimerQueue::new();
        let node = first_factory.create(Duration::ZERO, 10);
        let handle = node.downgrade().upgrade().unwrap();
        let node = second.insert(node).err().unwrap();
        assert!(first.insert(node).is_ok());
        assert!(second.remove(&handle).0.is_none());
        assert_eq!(first.count(), 1);
        assert!(first.pop_first().unwrap().ptr_eq(&handle));
        assert!(first.remove(&handle).0.is_none());
        let other = second_factory.create(Duration::ZERO, 20);
        assert!(second.insert(other).is_ok());
        assert_eq!(**second.first().unwrap(), 20);
    }

    #[test]
    fn old_owner_identity_survives_queue_drop() {
        let (mut queue, factory) = TimerQueue::new();
        let node = factory.create(Duration::ZERO, 1);
        let handle = node.downgrade().upgrade().unwrap();
        assert!(queue.insert(node).is_ok());
        drop(queue);
        drop(factory);
        assert!(!handle.0.link.is_linked());
        for _ in 0..4096 {
            let (mut replacement, factory) = TimerQueue::new();
            assert!(!Arc::ptr_eq(&replacement.owner, &handle.0.owner));
            assert!(
                replacement
                    .insert(factory.create(Duration::ZERO, 2))
                    .is_ok()
            );
            assert!(replacement.remove(&handle).0.is_none());
            assert_eq!(**replacement.first().unwrap(), 2);
        }
        let weak = handle.downgrade();
        drop(handle);
        assert!(weak.upgrade().is_none());
    }

    #[test]
    fn seeded_operations_match_an_ordered_reference() {
        for seed in 0..8 {
            let (mut queue, factory) = TimerQueue::new();
            let mut random = 0x53104000_u64 + seed;
            let mut handles = Vec::new();
            let mut live = BTreeMap::new();
            for step in 0..100000 {
                let next_fn = |state: &mut u64| {
                    *state ^= *state << 13;
                    *state ^= *state >> 7;
                    *state ^= *state << 17;
                    *state
                };
                match next_fn(&mut random) % 3 {
                    0 => {
                        let node =
                            factory.create(Duration::from_nanos(next_fn(&mut random) % 256), step);
                        let handle = node.downgrade().upgrade().unwrap();
                        live.insert(key(&handle), step);
                        assert!(queue.insert(node).is_ok());
                        handles.push(handle);
                    }
                    1 if !handles.is_empty() => {
                        let handle = &handles[next_fn(&mut random) as usize % handles.len()];
                        let expected = live.remove(&key(handle));
                        let removed = queue.remove(handle);
                        assert_eq!(removed.0.as_ref().map(|item| **item), expected);
                        assert!(!handle.0.link.is_linked());
                    }
                    _ => {
                        let expected = live.pop_first();
                        let actual = queue.pop_first();
                        assert_eq!(actual.as_ref().map(|item| (key(item), **item)), expected);
                        if let Some(actual) = actual {
                            assert!(!actual.0.link.is_linked());
                        }
                    }
                }
                let keys: Vec<_> = queue
                    .nodes
                    .iter()
                    .map(|node| {
                        assert!(node.link.is_linked());
                        assert!(Arc::ptr_eq(&node.owner, &queue.owner));
                        (node.deadline, node as *const Node<_> as usize)
                    })
                    .collect();
                assert_eq!(keys, live.keys().copied().collect::<Vec<_>>());
                #[cfg(feature = "validation")]
                assert_eq!(queue.snapshot().iter().map(key).collect::<Vec<_>>(), keys);
                assert_eq!(
                    queue.first().map(key),
                    live.first_key_value().map(|(key, _)| *key)
                );
            }
            while let Some(expected) = live.pop_first() {
                let actual = queue.pop_first().unwrap();
                assert_eq!((key(&actual), *actual), expected);
            }
            assert!(queue.first().is_none());
            assert!(queue.is_empty());
            assert!(handles.iter().all(|handle| !handle.0.link.is_linked()));
        }
    }

    struct ReleaseProbe {
        queue: Weak<Mutex<TimerQueue<ReleaseProbe>>>,
        drops: Arc<AtomicUsize>,
        unlocked: Arc<AtomicBool>,
    }

    impl Drop for ReleaseProbe {
        fn drop(&mut self) {
            self.drops.fetch_add(1, SeqCst);
            if let Some(queue) = self.queue.upgrade() {
                self.unlocked.store(queue.try_lock().is_ok(), SeqCst);
            }
        }
    }

    #[test]
    fn retirement_and_selection_leave_final_release_outside_the_lock() {
        let (queue, factory) = TimerQueue::new();
        let queue = Arc::new(Mutex::new(queue));
        let drops = Arc::new(AtomicUsize::new(0));
        let unlocked = Arc::new(AtomicBool::new(false));
        for selected in [false, true] {
            let node = factory.create(
                Duration::ZERO,
                ReleaseProbe {
                    queue: Arc::downgrade(&queue),
                    drops: drops.clone(),
                    unlocked: unlocked.clone(),
                },
            );
            let handle = node.downgrade().upgrade().unwrap();
            assert!(queue.lock().unwrap().insert(node).is_ok());
            let before = drops.load(SeqCst);
            let retired = if selected {
                (queue.lock().unwrap().pop_first(), None)
            } else {
                queue.lock().unwrap().remove(&handle)
            };
            drop(handle);
            assert_eq!(drops.load(SeqCst), before);
            unlocked.store(false, SeqCst);
            drop(retired);
            assert_eq!(drops.load(SeqCst), before + 1);
            assert!(unlocked.load(SeqCst));
        }
    }

    #[test]
    fn shared_queue_never_removes_a_generation_twice() {
        let (queue, factory) = TimerQueue::new();
        let queue = Arc::new(Mutex::new(queue));
        let completed = Arc::new(AtomicUsize::new(0));
        let removed = Arc::new(Mutex::new(HashSet::new()));
        let mut workers = Vec::new();
        for worker in 0..4 {
            let (queue, factory, completed, removed) = (
                queue.clone(),
                factory.clone(),
                completed.clone(),
                removed.clone(),
            );
            workers.push(thread::spawn(move || {
                for index in 0..4000 {
                    let node = factory.create(
                        Duration::from_nanos((index % 17) as u64),
                        worker * 4000 + index,
                    );
                    let handle = node.downgrade().upgrade().unwrap();
                    assert!(queue.lock().unwrap().insert(node).is_ok());
                    thread::yield_now();
                    let retired = queue.lock().unwrap().remove(&handle);
                    if let Some(actual) = retired.0.as_ref() {
                        assert!(removed.lock().unwrap().insert(**actual));
                    }
                    drop(retired);
                }
                completed.fetch_add(1, SeqCst);
            }));
        }
        while completed.load(SeqCst) != 4 || !queue.lock().unwrap().is_empty() {
            let selected = queue.lock().unwrap().pop_first();
            if let Some(actual) = selected {
                assert!(removed.lock().unwrap().insert(*actual));
            }
            thread::yield_now();
        }
        for worker in workers {
            worker.join().unwrap();
        }
        assert_eq!(removed.lock().unwrap().len(), 16000);
        assert!(queue.lock().unwrap().first().is_none());
    }
}
