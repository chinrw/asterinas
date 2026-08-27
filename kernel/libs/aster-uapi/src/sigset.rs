// SPDX-License-Identifier: MPL-2.0

use core::mem::offset_of;

use ostd_pod::{FromBytes, Immutable, IntoBytes, KnownLayout};

/// The syscall-visible size of a Linux signal set.
pub const USER_SIGSET_SIZE: usize = size_of::<CUserSigSet>();

/// The target-specific layout of [`CUserSigSet`].
pub const USER_SIGSET_LAYOUT: [usize; 3] = [
    size_of::<CUserSigSet>(),
    align_of::<CUserSigSet>(),
    offset_of!(CUserSigSet, bits),
];

/// An error returned when a syscall receives the wrong signal-set size.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SigsetSizeError {
    /// Linux requires the size to match the complete syscall signal set.
    InvalidSize,
}

/// A Linux signal set copied through the syscall ABI.
///
/// Linux exposes 64 signals through the generic UAPI, so the syscall bitset
/// contains one 64-bit word on the supported Asterinas architectures.
/// Reference: <https://github.com/torvalds/linux/blob/v6.18/include/uapi/asm-generic/signal.h>.
#[repr(transparent)]
#[derive(
    Clone, Copy, Debug, Default, Eq, PartialEq, FromBytes, Immutable, IntoBytes, KnownLayout,
)]
pub struct CUserSigSet {
    bits: u64,
}

impl CUserSigSet {
    /// Creates a signal set from its raw ABI bits.
    pub const fn new(bits: u64) -> Self {
        Self { bits }
    }

    /// Returns the raw ABI bits.
    pub const fn bits(self) -> u64 {
        self.bits
    }
}

/// Validates the signal-set size supplied to a Linux syscall.
pub const fn validate_sigset_size(size: usize) -> Result<(), SigsetSizeError> {
    if size == USER_SIGSET_SIZE {
        Ok(())
    } else {
        Err(SigsetSizeError::InvalidSize)
    }
}

#[cfg(test)]
mod tests {
    use ostd_pod::IntoBytes;

    use super::{
        CUserSigSet, SigsetSizeError, USER_SIGSET_LAYOUT, USER_SIGSET_SIZE, validate_sigset_size,
    };

    #[test]
    fn sigset_size_validation_is_exact() {
        assert_eq!(validate_sigset_size(USER_SIGSET_SIZE), Ok(()));

        for size in [0, USER_SIGSET_SIZE - 1, USER_SIGSET_SIZE + 1, usize::MAX] {
            assert_eq!(
                validate_sigset_size(size),
                Err(SigsetSizeError::InvalidSize)
            );
        }
    }

    #[test]
    fn user_sigset_layout_matches_the_linux_abi() {
        let bits = 0x0102_0304_0506_0708u64;
        let sigset = CUserSigSet::new(bits);

        assert_eq!(USER_SIGSET_SIZE, 8);
        assert_eq!(USER_SIGSET_LAYOUT, [8, 8, 0]);
        assert_eq!(sigset.bits(), bits);
        assert_eq!(sigset.as_bytes(), &bits.to_ne_bytes());
    }
}

#[cfg(kani)]
mod proofs {
    use super::{SigsetSizeError, USER_SIGSET_SIZE, validate_sigset_size};

    #[kani::proof]
    fn proof_sigset_size_matches_linux_abi() {
        let size: usize = kani::any();
        let result = validate_sigset_size(size);

        if size == USER_SIGSET_SIZE {
            assert_eq!(result, Ok(()));
        } else {
            assert_eq!(result, Err(SigsetSizeError::InvalidSize));
        }
    }
}
