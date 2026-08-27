// SPDX-License-Identifier: MPL-2.0

//! Checked arithmetic for Linux `mmap` and `mremap` inputs.
//!
//! Linux aligns lengths before checking address-space limits and represents
//! mappings as half-open ranges. The syscall layer maps these pure arithmetic
//! results to operation-specific errors.
//!
//! References:
//! - <https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/mm/mmap.c?h=v6.18.45>
//! - <https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/mm/mremap.c?h=v6.18.45>

/// Aligns a value upward without wrapping.
///
/// The alignment must be a nonzero power of two.
pub const fn checked_page_align(value: usize, page_size: usize) -> Option<usize> {
    if !page_size.is_power_of_two() {
        return None;
    }

    let mask = page_size - 1;
    let Some(rounded) = value.checked_add(mask) else {
        return None;
    };
    Some(rounded & !mask)
}

/// A half-open address range whose end neither wraps nor exceeds a declared limit.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CheckedAddressRange {
    start: usize,
    end: usize,
}

impl CheckedAddressRange {
    /// Creates `[start, start + len)` when the end is at most `max_end`.
    pub const fn new(start: usize, len: usize, max_end: usize) -> Option<Self> {
        let Some(end) = start.checked_add(len) else {
            return None;
        };
        if end > max_end {
            return None;
        }
        Some(Self { start, end })
    }

    /// Returns the inclusive start address.
    pub const fn start(self) -> usize {
        self.start
    }

    /// Returns the exclusive end address.
    pub const fn end(self) -> usize {
        self.end
    }

    /// Returns the range length.
    pub const fn len(self) -> usize {
        self.end - self.start
    }

    /// Returns whether the range contains no addresses.
    pub const fn is_empty(self) -> bool {
        self.start == self.end
    }

    /// Returns whether two nonempty half-open ranges intersect.
    pub const fn overlaps(self, other: Self) -> bool {
        !self.is_empty() && !other.is_empty() && self.end > other.start && other.end > self.start
    }
}

#[cfg(test)]
mod tests {
    use super::{CheckedAddressRange, checked_page_align};

    #[test]
    fn page_alignment_is_checked_and_idempotent() {
        assert_eq!(checked_page_align(0, 4096), Some(0));
        assert_eq!(checked_page_align(1, 4096), Some(4096));
        assert_eq!(checked_page_align(4096, 4096), Some(4096));
        assert_eq!(checked_page_align(usize::MAX, 4096), None);
        assert_eq!(checked_page_align(1, 0), None);
        assert_eq!(checked_page_align(1, 3), None);
    }

    #[test]
    fn address_range_rejects_wrap_and_end_beyond_limit() {
        let range = CheckedAddressRange::new(0x1000, 0x2000, 0x4000).unwrap();

        assert_eq!(range.start(), 0x1000);
        assert_eq!(range.end(), 0x3000);
        assert_eq!(range.len(), 0x2000);
        assert!(!range.is_empty());
        assert_eq!(CheckedAddressRange::new(usize::MAX, 1, usize::MAX), None);
        assert_eq!(CheckedAddressRange::new(0x3000, 0x2000, 0x4000), None);
    }

    #[test]
    fn address_range_overlap_uses_half_open_intervals() {
        let range = CheckedAddressRange::new(0x1000, 0x1000, usize::MAX).unwrap();
        let touching = CheckedAddressRange::new(0x2000, 0x1000, usize::MAX).unwrap();
        let overlapping = CheckedAddressRange::new(0x1fff, 0x1000, usize::MAX).unwrap();
        let empty = CheckedAddressRange::new(0x1800, 0, usize::MAX).unwrap();

        assert!(!range.overlaps(touching));
        assert!(range.overlaps(overlapping));
        assert!(overlapping.overlaps(range));
        assert!(!range.overlaps(empty));
        assert!(!empty.overlaps(range));
    }
}

#[cfg(kani)]
mod proofs {
    use super::{CheckedAddressRange, checked_page_align};

    #[kani::proof]
    fn proof_page_alignment_does_not_wrap() {
        let value: usize = kani::any();
        let page_size: usize = kani::any();
        let result = checked_page_align(value, page_size);

        if page_size.is_power_of_two() {
            let mask = page_size - 1;
            match result {
                Some(aligned) => {
                    assert!(aligned >= value);
                    assert_eq!(aligned & mask, 0);
                    assert!(aligned - value < page_size);
                }
                None => assert!(value.checked_add(mask).is_none()),
            }
        } else {
            assert_eq!(result, None);
        }
    }

    #[kani::proof]
    fn proof_checked_address_range_preserves_length() {
        let start: usize = kani::any();
        let len: usize = kani::any();
        let max_end: usize = kani::any();
        let result = CheckedAddressRange::new(start, len, max_end);

        match result {
            Some(range) => {
                assert_eq!(range.start(), start);
                assert_eq!(range.len(), len);
                assert!(range.end() <= max_end);
            }
            None => {
                if let Some(end) = start.checked_add(len) {
                    assert!(end > max_end);
                }
            }
        }
    }

    #[kani::proof]
    fn proof_address_range_overlap_is_half_open() {
        let first_start: usize = kani::any();
        let first_len: usize = kani::any();
        let second_start: usize = kani::any();
        let second_len: usize = kani::any();

        let Some(first) = CheckedAddressRange::new(first_start, first_len, usize::MAX) else {
            return;
        };
        let Some(second) = CheckedAddressRange::new(second_start, second_len, usize::MAX) else {
            return;
        };

        assert_eq!(first.overlaps(second), second.overlaps(first));
        if first.is_empty()
            || second.is_empty()
            || first.end() == second.start()
            || second.end() == first.start()
        {
            assert!(!first.overlaps(second));
        }
    }
}
