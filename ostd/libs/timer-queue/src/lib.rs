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

/// Creates generations belonging to one queue without acquiring its lock.
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

/// Orders callbacks by deadline and identity, with a cached minimum.
///
/// Callers serialize mutations through exclusive access, normally a queue lock.
/// Only this module can link nodes. A fixed, immutable key makes comparison
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
}
