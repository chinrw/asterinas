// SPDX-License-Identifier: MPL-2.0

//! Pure Linux UAPI types and validation helpers shared by syscall code and verification tools.
#![no_std]
#![deny(unsafe_code)]

use core::mem::offset_of;

use ostd_pod::{FromBytes, Immutable, IntoBytes, KnownLayout};

/// The maximum number of buffers in one Linux I/O vector.
///
/// Reference: <https://elixir.bootlin.com/linux/v6.16/source/include/uapi/linux/uio.h#L46>.
pub const MAX_IO_VECTOR_LENGTH: usize = 1024;

/// The maximum aggregate byte count accepted by Linux vectored I/O.
pub const MAX_TOTAL_IOV_BYTES: usize = isize::MAX as usize;

/// The target-specific layout of [`UserIoVec`].
///
/// Verification probes export these values from target object files, avoiding host-layout guesses.
pub const USER_IOVEC_LAYOUT: [usize; 4] = [
    size_of::<UserIoVec>(),
    align_of::<UserIoVec>(),
    offset_of!(UserIoVec, base),
    offset_of!(UserIoVec, len),
];

#[cfg(feature = "syssec-layout")]
#[used]
#[allow(
    unsafe_code,
    reason = "the verification-only section exports constants and performs no memory access"
)]
#[unsafe(link_section = ".syssec.layout")]
static SYSSEC_LAYOUT_USER_IOVEC: [u64; 7] = [
    u64::from_le_bytes(*b"SYSLAY01"),
    1,
    size_of::<usize>() as u64 * 8,
    USER_IOVEC_LAYOUT[0] as u64,
    USER_IOVEC_LAYOUT[1] as u64,
    USER_IOVEC_LAYOUT[2] as u64,
    USER_IOVEC_LAYOUT[3] as u64,
];

/// Calculates the address of one user I/O vector without wrapping.
pub const fn iovec_entry_addr(start_addr: usize, index: usize) -> Option<usize> {
    let Some(offset) = index.checked_mul(size_of::<UserIoVec>()) else {
        return None;
    };
    start_addr.checked_add(offset)
}

/// Truncates one segment to the remaining aggregate byte budget.
pub const fn truncate_iovec_len(len: usize, remaining: usize) -> (usize, usize) {
    let effective_len = if len > remaining { remaining } else { len };
    (effective_len, remaining - effective_len)
}

/// An error returned while validating a user-provided I/O vector.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IovecError {
    /// The ABI length cannot be represented by the validated unsigned type.
    NegativeLength,
}

/// An I/O vector copied from the Linux syscall ABI.
#[repr(C)]
#[derive(Clone, Copy, Debug, FromBytes, Immutable, IntoBytes, KnownLayout)]
pub struct UserIoVec {
    base: usize,
    len: isize,
}

impl UserIoVec {
    /// Creates an ABI I/O vector from its raw fields.
    pub const fn new(base: usize, len: isize) -> Self {
        Self { base, len }
    }

    /// Validates signed ABI fields and returns an internal I/O vector.
    pub const fn validate(self) -> Result<IoVec, IovecError> {
        if self.len < 0 {
            return Err(IovecError::NegativeLength);
        }
        Ok(IoVec {
            base: self.base,
            len: self.len as usize,
        })
    }
}

/// A validated I/O vector with an unsigned length.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct IoVec {
    base: usize,
    len: usize,
}

impl IoVec {
    /// Returns the user virtual address.
    pub const fn base(self) -> usize {
        self.base
    }

    /// Returns the validated buffer length.
    pub const fn len(self) -> usize {
        self.len
    }

    /// Returns whether this vector contributes no accessible bytes.
    pub const fn is_empty(self) -> bool {
        self.len == 0 || self.base == 0
    }

    /// Truncates this segment and returns the remaining aggregate byte budget.
    pub fn truncate_to(&mut self, remaining: usize) -> usize {
        let (effective_len, remaining) = truncate_iovec_len(self.len, remaining);
        self.len = effective_len;
        remaining
    }
}

#[cfg(test)]
mod tests {
    use core::mem::offset_of;

    use ostd_pod::IntoBytes;

    use super::{IovecError, UserIoVec, iovec_entry_addr, truncate_iovec_len};

    #[test]
    fn iovec_length_is_truncated_to_remaining_total() {
        assert_eq!(truncate_iovec_len(8, 5), (5, 0));
    }

    #[test]
    fn overflowing_iovec_entry_address_is_rejected() {
        assert_eq!(iovec_entry_addr(usize::MAX - 7, 1), None);
    }

    #[test]
    fn negative_iovec_length_is_rejected() {
        let user_iovec = UserIoVec::new(0x1000, -1);

        assert_eq!(user_iovec.validate(), Err(IovecError::NegativeLength));
    }

    #[test]
    fn user_iovec_layout_matches_the_linux_abi() {
        assert_eq!(size_of::<UserIoVec>(), size_of::<usize>() * 2);
        assert_eq!(align_of::<UserIoVec>(), align_of::<usize>());
        assert_eq!(offset_of!(UserIoVec, base), 0);
        assert_eq!(offset_of!(UserIoVec, len), size_of::<usize>());
    }

    #[test]
    fn user_iovec_bytes_are_fully_initialized() {
        let base = 0x0102_0304usize;
        let len = 0x0506_0708isize;
        let user_iovec = UserIoVec::new(base, len);
        let bytes = user_iovec.as_bytes();

        assert_eq!(&bytes[..size_of::<usize>()], &base.to_ne_bytes());
        assert_eq!(&bytes[size_of::<usize>()..], &len.to_ne_bytes());
    }
}

#[cfg(kani)]
mod proofs {
    use super::{
        IovecError, MAX_IO_VECTOR_LENGTH, UserIoVec, iovec_entry_addr, truncate_iovec_len,
    };

    #[kani::proof]
    fn proof_negative_iovec_length_is_rejected() {
        let base = kani::any();
        let len: isize = kani::any();
        kani::assume(len < 0);

        assert_eq!(
            UserIoVec::new(base, len).validate(),
            Err(IovecError::NegativeLength)
        );
    }

    #[kani::proof]
    fn proof_nonnegative_iovec_length_preserves_value() {
        let base = kani::any();
        let len: isize = kani::any();
        kani::assume(len >= 0);

        let iovec = UserIoVec::new(base, len).validate().unwrap();
        assert_eq!(iovec.base(), base);
        assert_eq!(iovec.len(), len as usize);
    }

    #[kani::proof]
    fn proof_iovec_entry_address_no_wrap() {
        let start_addr: usize = kani::any();
        let index: usize = kani::any();

        if let Some(address) = iovec_entry_addr(start_addr, index) {
            let offset = index
                .checked_mul(size_of::<UserIoVec>())
                .expect("a successful address includes a valid offset");
            assert!(address >= start_addr);
            assert_eq!(address - start_addr, offset);
        }
    }

    #[kani::proof]
    fn proof_supported_iovec_index_offset_does_not_wrap() {
        let count: usize = kani::any();
        let index: usize = kani::any();
        kani::assume(count <= MAX_IO_VECTOR_LENGTH);
        kani::assume(index < count);

        assert!(index.checked_mul(size_of::<UserIoVec>()).is_some());
    }

    #[kani::proof]
    fn proof_iovec_truncation_preserves_the_remaining_budget() {
        let len: usize = kani::any();
        let remaining: usize = kani::any();

        let (effective_len, next_remaining) = truncate_iovec_len(len, remaining);
        assert!(effective_len <= len);
        assert!(effective_len <= remaining);
        assert!(next_remaining <= remaining);
        assert_eq!(effective_len.checked_add(next_remaining), Some(remaining));
    }
}
