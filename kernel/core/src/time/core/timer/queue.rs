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

    #[cfg(any(ktest, test))]
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

#[cfg(any(ktest, test))]
mod tests {
    use alloc::{
        collections::{BTreeMap, BTreeSet},
        sync::Weak,
        vec::Vec,
    };
    use core::time::Duration;

    #[cfg(ktest)]
    use ostd::prelude::ktest;

    use super::*;

    fn callback(deadline: u64) -> Arc<TimerCallback> {
        Arc::new(TimerCallback::new(
            Duration::from_nanos(deadline),
            Weak::new(),
        ))
    }

    fn check(queue: &TimerQueue) {
        fn walk(
            queue: &TimerQueue,
            position: usize,
            parent: usize,
            depth: u32,
            seen: &mut BTreeSet<usize>,
            order: &mut Vec<usize>,
        ) -> usize {
            if position == NONE {
                return 1;
            }
            assert!(depth <= 2 * usize::BITS);
            assert!(
                seen.insert(position),
                "tree contains a cycle or duplicate child"
            );
            let node = queue.node(position);
            assert_eq!(node.parent, parent);
            assert_eq!(
                node.callback.queue_position.load(Ordering::Relaxed),
                position
            );
            if node.red {
                assert!(!queue.is_red(node.left));
                assert!(!queue.is_red(node.right));
            }
            let left = walk(queue, node.left, position, depth + 1, seen, order);
            order.push(position);
            let right = walk(queue, node.right, position, depth + 1, seen, order);
            assert_eq!(left, right, "unequal black heights");
            left + usize::from(!node.red)
        }
        assert!(!queue.is_red(queue.root));
        let mut live = BTreeSet::new();
        let mut order = Vec::new();
        walk(queue, queue.root, NONE, 0, &mut live, &mut order);
        assert_eq!(live.len(), queue.len);
        assert!(order.windows(2).all(|pair| queue.node(pair[0]).callback.key() < queue.node(pair[1]).callback.key()));
        assert_eq!(order.first().copied().unwrap_or(NONE), queue.earliest);
        assert_eq!(queue.root == NONE, queue.len == 0);
        let mut free = BTreeSet::new();
        let mut current = queue.free;
        while current != NONE {
            assert!(free.insert(current), "free list contains a cycle");
            assert!(!live.contains(&current));
            let Slot::Vacant { next } = queue.slots[current] else {
                panic!("free slot is occupied");
            };
            current = next;
        }
        assert_eq!(live.len() + free.len(), queue.slots.len());
        for (position, slot) in queue.slots.iter().enumerate() {
            match slot {
                Slot::Occupied(_) => assert!(live.contains(&position)),
                Slot::Vacant { .. } => assert!(free.contains(&position)),
            }
        }
    }

    fn permutations(values: &mut [usize], at: usize, output: &mut Vec<Vec<usize>>) {
        if at == values.len() {
            output.push(values.to_vec());
            return;
        }
        for next in at..values.len() {
            values.swap(at, next);
            permutations(values, at + 1, output);
            values.swap(at, next);
        }
    }

    #[cfg_attr(ktest, ktest)]
    #[cfg_attr(test, test)]
    fn all_small_arena_insertion_and_deletion_orders() {
        for size in 0..=5 {
            let mut orders = Vec::new();
            permutations(&mut (0..size).collect::<Vec<_>>(), 0, &mut orders);
            for insertion in &orders {
                for deletion in &orders {
                    let handles: Vec<_> = (0..size).map(|index| callback(index as u64)).collect();
                    let mut queue = TimerQueue::new();
                    for &index in insertion {
                        queue.insert(handles[index].clone());
                        check(&queue);
                    }
                    for &index in deletion {
                        assert!(Arc::ptr_eq(
                            &queue.remove(&handles[index]).unwrap(),
                            &handles[index]
                        ));
                        assert_eq!(
                            handles[index].queue_position.load(Ordering::Relaxed),
                            NOT_QUEUED
                        );
                        assert!(queue.remove(&handles[index]).is_none());
                        check(&queue);
                    }
                }
            }
        }
    }

    #[cfg_attr(ktest, ktest)]
    #[cfg_attr(test, test)]
    fn arena_seeded_operations_match_an_ordered_reference() {
        for seed in 0..8 {
            let mut random = 0x53104000_u64 + seed;
            let mut queue = TimerQueue::new();
            let mut handles = Vec::new();
            let mut live = BTreeMap::new();
            for _ in 0..100000 {
                let next_fn = |state: &mut u64| {
                    *state ^= *state << 13;
                    *state ^= *state >> 7;
                    *state ^= *state << 17;
                    *state
                };
                match next_fn(&mut random) % 3 {
                    0 => {
                        let callback = callback(next_fn(&mut random) % 256);
                        live.insert(callback.key(), ());
                        queue.insert(callback.clone());
                        handles.push(callback);
                    }
                    1 if !handles.is_empty() => {
                        let callback = &handles[next_fn(&mut random) as usize % handles.len()];
                        let expected = live.remove(&callback.key());
                        assert_eq!(queue.remove(callback).is_some(), expected.is_some());
                        assert_eq!(callback.queue_position.load(Ordering::Relaxed), NOT_QUEUED);
                    }
                    _ => {
                        let expected = live.pop_first().map(|(key, ())| key);
                        let selected = queue.pop();
                        assert_eq!(selected.as_ref().map(|callback| callback.key()), expected);
                        if let Some(selected) = selected {
                            assert_eq!(selected.queue_position.load(Ordering::Relaxed), NOT_QUEUED);
                        }
                    }
                }
                check(&queue);
                assert_eq!(queue.len(), live.len());
                assert_eq!(
                    queue.first().map(|callback| callback.key()),
                    live.first_key_value().map(|(key, ())| *key)
                );
            }
            while let Some((expected, ())) = live.pop_first() {
                assert_eq!(queue.pop().unwrap().key(), expected);
                check(&queue);
            }
            assert!(queue.first().is_none());
            assert!(
                handles
                    .iter()
                    .all(|callback| callback.queue_position.load(Ordering::Relaxed) == NOT_QUEUED)
            );
        }
    }

    #[cfg_attr(ktest, ktest)]
    #[cfg_attr(test, test)]
    fn vacant_slots_do_not_resurrect_retired_arena_handles() {
        let mut queue = TimerQueue::new();
        let old: Vec<_> = (0..128).map(callback).collect();
        for callback in &old {
            queue.insert(callback.clone());
        }
        let retired_positions: BTreeSet<_> = old
            .iter()
            .step_by(2)
            .map(|callback| callback.queue_position.load(Ordering::Relaxed))
            .collect();
        for callback in old.iter().step_by(2) {
            queue.remove(callback);
        }
        check(&queue);
        let replacements: Vec<_> = (0..64).map(|_| callback(0)).collect();
        for callback in &replacements {
            queue.insert(callback.clone());
        }
        assert_eq!(queue.slots.len(), 128);
        assert_eq!(
            replacements
                .iter()
                .map(|callback| callback.queue_position.load(Ordering::Relaxed))
                .collect::<BTreeSet<_>>(),
            retired_positions
        );
        for callback in old.iter().step_by(2) {
            assert!(queue.remove(callback).is_none());
        }
        check(&queue);
        drop(queue);
        assert!(
            old.iter()
                .chain(replacements.iter())
                .all(|callback| callback.queue_position.load(Ordering::Relaxed) == NOT_QUEUED)
        );
    }

    #[cfg_attr(ktest, ktest)]
    #[cfg_attr(test, test)]
    fn arena_growth_and_drain_preserve_the_free_list() {
        let mut queue = TimerQueue::new();
        let handles: Vec<_> = (0..8193).map(|index| callback(8193 - index)).collect();
        for (index, callback) in handles.iter().enumerate() {
            queue.insert(callback.clone());
            if [1, 4, 5, 8, 9, 64, 65, 4096, 4097, 8192, 8193].contains(&(index + 1)) {
                check(&queue);
            }
        }
        let capacity = queue.slots.capacity();
        for callback in handles.iter().step_by(3) {
            queue.remove(callback);
        }
        check(&queue);
        let mut previous = Duration::ZERO;
        while let Some(callback) = queue.pop() {
            assert!(callback.expired_time >= previous);
            previous = callback.expired_time;
            assert_eq!(callback.queue_position.load(Ordering::Relaxed), NOT_QUEUED);
            if queue.len % 64 == 0 {
                check(&queue);
            }
        }
        check(&queue);
        assert_eq!(queue.slots.len(), 8193);
        assert_eq!(queue.slots.capacity(), capacity);
        for callback in &handles {
            queue.insert(callback.clone());
        }
        check(&queue);
        assert_eq!(queue.slots.capacity(), capacity);
        drop(queue);
        assert!(
            handles
                .iter()
                .all(|callback| callback.queue_position.load(Ordering::Relaxed) == NOT_QUEUED)
        );
    }
}
