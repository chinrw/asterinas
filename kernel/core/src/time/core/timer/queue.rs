// SPDX-License-Identifier: MPL-2.0

//! A timer tree whose stable slot indices identify queued callbacks.

use alloc::{sync::Arc, vec::Vec};
use core::sync::atomic::Ordering;

use super::TimerCallback;

pub(super) const NOT_QUEUED: usize = usize::MAX;
const NONE: usize = NOT_QUEUED;

struct Node {
    callback: Arc<TimerCallback>,
    parent: usize,
    left: usize,
    right: usize,
    red: bool,
}

enum Slot {
    Occupied(Node),
    Vacant { next: usize },
}

pub(super) struct TimerQueue {
    slots: Vec<Slot>,
    root: usize,
    earliest: usize,
    free: usize,
    len: usize,
}

impl TimerQueue {
    pub(super) fn new() -> Self {
        Self {
            slots: Vec::new(),
            root: NONE,
            earliest: NONE,
            free: NONE,
            len: 0,
        }
    }

    pub(super) fn is_empty(&self) -> bool {
        self.len == 0
    }

    #[cfg(ktest)]
    pub(super) fn len(&self) -> usize {
        self.len
    }

    pub(super) fn first(&self) -> Option<&Arc<TimerCallback>> {
        (self.earliest != NONE).then(|| &self.node(self.earliest).callback)
    }

    pub(super) fn insert(&mut self, callback: Arc<TimerCallback>) {
        debug_assert_eq!(callback.queue_position.load(Ordering::Relaxed), NOT_QUEUED);
        let key = callback.key();
        let mut parent = NONE;
        let mut current = self.root;
        let mut left = false;
        while current != NONE {
            parent = current;
            left = key < self.node(current).callback.key();
            current = self.child(current, left);
        }
        let node = Node {
            callback,
            parent,
            left: NONE,
            right: NONE,
            red: true,
        };
        let position = if self.free == NONE {
            let position = self.slots.len();
            // Slot is non-zero-sized, so a valid Vec index cannot equal NONE.
            self.slots.push(Slot::Occupied(node));
            position
        } else {
            let position = self.free;
            let Slot::Vacant { next } = self.slots[position] else {
                unreachable!("free list points to an occupied timer slot");
            };
            self.free = next;
            self.slots[position] = Slot::Occupied(node);
            position
        };
        if parent == NONE {
            self.root = position;
        } else {
            self.set_child(parent, left, position);
        }
        self.node(position)
            .callback
            .queue_position
            .store(position, Ordering::Relaxed);
        if self.earliest == NONE || key < self.node(self.earliest).callback.key() {
            self.earliest = position;
        }
        self.len += 1;
        self.fix_insert(position);
    }

    pub(super) fn remove(&mut self, callback: &Arc<TimerCallback>) -> Option<Arc<TimerCallback>> {
        // The owning manager's queue lock protects both the slot and position.
        let position = callback.queue_position.load(Ordering::Relaxed);
        if position == NOT_QUEUED {
            return None;
        }
        debug_assert!(Arc::ptr_eq(&self.node(position).callback, callback));
        Some(self.remove_at(position))
    }

    pub(super) fn pop(&mut self) -> Option<Arc<TimerCallback>> {
        (self.earliest != NONE).then(|| self.remove_at(self.earliest))
    }

    fn node(&self, position: usize) -> &Node {
        match &self.slots[position] {
            Slot::Occupied(node) => node,
            Slot::Vacant { .. } => unreachable!("tree points to a vacant timer slot"),
        }
    }

    fn node_mut(&mut self, position: usize) -> &mut Node {
        match &mut self.slots[position] {
            Slot::Occupied(node) => node,
            Slot::Vacant { .. } => unreachable!("tree points to a vacant timer slot"),
        }
    }

    fn child(&self, position: usize, left: bool) -> usize {
        if position == NONE {
            NONE
        } else if left {
            self.node(position).left
        } else {
            self.node(position).right
        }
    }

    fn set_child(&mut self, position: usize, left: bool, child: usize) {
        if left {
            self.node_mut(position).left = child;
        } else {
            self.node_mut(position).right = child;
        }
    }

    fn is_red(&self, position: usize) -> bool {
        position != NONE && self.node(position).red
    }

    fn set_red(&mut self, position: usize, red: bool) {
        if position != NONE {
            self.node_mut(position).red = red;
        }
    }

    // Move the right child up for a left rotation, or the left child for a right one.
    fn rotate(&mut self, position: usize, left: bool) {
        let promoted = self.child(position, !left);
        debug_assert_ne!(promoted, NONE);
        let middle = self.child(promoted, left);
        self.set_child(position, !left, middle);
        if middle != NONE {
            self.node_mut(middle).parent = position;
        }
        let parent = self.node(position).parent;
        self.node_mut(promoted).parent = parent;
        if parent == NONE {
            self.root = promoted;
        } else {
            let was_left = self.node(parent).left == position;
            self.set_child(parent, was_left, promoted);
        }
        self.set_child(promoted, left, position);
        self.node_mut(position).parent = promoted;
    }

    fn fix_insert(&mut self, mut position: usize) {
        while self.is_red(self.node(position).parent) {
            let mut parent = self.node(position).parent;
            let grandparent = self.node(parent).parent;
            let parent_is_left = self.node(grandparent).left == parent;
            let uncle = self.child(grandparent, !parent_is_left);
            if self.is_red(uncle) {
                self.set_red(parent, false);
                self.set_red(uncle, false);
                self.set_red(grandparent, true);
                position = grandparent;
                continue;
            }
            if position == self.child(parent, !parent_is_left) {
                position = parent;
                self.rotate(position, parent_is_left);
                parent = self.node(position).parent;
            }
            self.set_red(parent, false);
            self.set_red(grandparent, true);
            self.rotate(grandparent, !parent_is_left);
        }
        self.set_red(self.root, false);
    }

    fn minimum(&self, mut position: usize) -> usize {
        while self.node(position).left != NONE {
            position = self.node(position).left;
        }
        position
    }

    fn successor(&self, position: usize) -> usize {
        let right = self.node(position).right;
        if right != NONE {
            return self.minimum(right);
        }
        let mut current = position;
        let mut parent = self.node(current).parent;
        while parent != NONE && self.node(parent).right == current {
            current = parent;
            parent = self.node(current).parent;
        }
        parent
    }

    fn transplant(&mut self, old: usize, new: usize) {
        let parent = self.node(old).parent;
        if parent == NONE {
            self.root = new;
        } else {
            self.set_child(parent, self.node(parent).left == old, new);
        }
        if new != NONE {
            self.node_mut(new).parent = parent;
        }
    }

    fn remove_at(&mut self, position: usize) -> Arc<TimerCallback> {
        if self.earliest == position {
            self.earliest = self.successor(position);
        }
        let left = self.node(position).left;
        let right = self.node(position).right;
        let mut removed_red = self.is_red(position);
        let replacement;
        let replacement_parent;
        if left == NONE {
            replacement = right;
            replacement_parent = self.node(position).parent;
            self.transplant(position, right);
        } else if right == NONE {
            replacement = left;
            replacement_parent = self.node(position).parent;
            self.transplant(position, left);
        } else {
            // Move the successor's slot, keeping both callback identities fixed.
            let successor = self.minimum(right);
            removed_red = self.is_red(successor);
            replacement = self.node(successor).right;
            if self.node(successor).parent == position {
                replacement_parent = successor;
            } else {
                replacement_parent = self.node(successor).parent;
                self.transplant(successor, replacement);
                self.node_mut(successor).right = right;
                self.node_mut(right).parent = successor;
            }
            self.transplant(position, successor);
            self.node_mut(successor).left = left;
            self.node_mut(left).parent = successor;
            self.node_mut(successor).red = self.is_red(position);
        }
        let Slot::Occupied(removed) =
            core::mem::replace(&mut self.slots[position], Slot::Vacant { next: self.free })
        else {
            unreachable!("removing a vacant timer slot");
        };
        removed
            .callback
            .queue_position
            .store(NOT_QUEUED, Ordering::Relaxed);
        self.free = position;
        self.len -= 1;
        if !removed_red {
            self.fix_remove(replacement, replacement_parent);
        }
        removed.callback
    }

    fn fix_remove(&mut self, mut position: usize, mut parent: usize) {
        while position != self.root && !self.is_red(position) {
            let position_is_left = self.node(parent).left == position;
            let mut sibling = self.child(parent, !position_is_left);
            if self.is_red(sibling) {
                self.set_red(sibling, false);
                self.set_red(parent, true);
                self.rotate(parent, position_is_left);
                sibling = self.child(parent, !position_is_left);
            }
            if !self.is_red(self.child(sibling, true)) && !self.is_red(self.child(sibling, false)) {
                self.set_red(sibling, true);
                position = parent;
                parent = self.node(position).parent;
                continue;
            }
            if !self.is_red(self.child(sibling, !position_is_left)) {
                self.set_red(self.child(sibling, position_is_left), false);
                self.set_red(sibling, true);
                self.rotate(sibling, !position_is_left);
                sibling = self.child(parent, !position_is_left);
            }
            self.set_red(sibling, self.is_red(parent));
            self.set_red(parent, false);
            self.set_red(self.child(sibling, !position_is_left), false);
            self.rotate(parent, position_is_left);
            position = self.root;
        }
        self.set_red(position, false);
    }
}

impl Drop for TimerQueue {
    fn drop(&mut self) {
        for slot in &self.slots {
            if let Slot::Occupied(node) = slot {
                node.callback
                    .queue_position
                    .store(NOT_QUEUED, Ordering::Relaxed);
            }
        }
    }
}
