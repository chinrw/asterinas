// SPDX-License-Identifier: MPL-2.0

//! Signal sets and atomic masks.
//!
//! A signal set is a bit-set of signals. A signal mask is a set of signals
//! that are blocked from delivery to a thread. An atomic signal mask
//! implementation is provided for shared access to signal masks.

use core::{
    fmt::LowerHex,
    ops,
    sync::atomic::{AtomicU64, Ordering},
};

use aster_uapi::{CUserSigSet as RawCUserSigSet, validate_sigset_size as validate_abi_sigset_size};
use atomic_integer_wrapper::define_atomic_version_of_integer_like_type;

use super::{constants::MIN_STD_SIG_NUM, sig_num::SigNum};
use crate::prelude::*;

/// A signal mask.
///
/// This is an alias to the [`SigSet`]. All the signal in the set are blocked
/// from the delivery to a thread.
pub(crate) type SigMask = SigSet;

/// Validates a syscall signal-set size and maps the UAPI error to `EINVAL`.
pub(crate) fn validate_sigset_size(size: usize) -> Result<()> {
    validate_abi_sigset_size(size)
        .map_err(|_| Error::with_message(Errno::EINVAL, "invalid sigset size"))
}

/// A bit-set of signals.
///
/// Because that all the signal numbers are in the range of 1 to 64, casting
/// a signal set from `u64` to `SigSet` will always succeed.
#[repr(transparent)]
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Pod)]
pub(crate) struct SigSet(RawCUserSigSet);

impl From<SigNum> for SigSet {
    fn from(signum: SigNum) -> Self {
        let idx = signum.as_u8() - MIN_STD_SIG_NUM;
        Self::from_bits(1_u64 << idx)
    }
}

impl From<u64> for SigSet {
    fn from(bits: u64) -> Self {
        Self::from_bits(bits)
    }
}

impl From<SigSet> for u64 {
    fn from(set: SigSet) -> u64 {
        set.bits()
    }
}

impl<T: Into<SigSet>> ops::BitAnd<T> for SigSet {
    type Output = Self;

    fn bitand(self, rhs: T) -> Self {
        Self::from_bits(self.bits() & rhs.into().bits())
    }
}

impl<T: Into<SigSet>> ops::BitAndAssign<T> for SigSet {
    fn bitand_assign(&mut self, rhs: T) {
        *self = Self::from_bits(self.bits() & rhs.into().bits());
    }
}

impl<T: Into<SigSet>> ops::BitOr<T> for SigSet {
    type Output = Self;

    fn bitor(self, rhs: T) -> Self {
        Self::from_bits(self.bits() | rhs.into().bits())
    }
}

impl<T: Into<SigSet>> ops::BitOrAssign<T> for SigSet {
    fn bitor_assign(&mut self, rhs: T) {
        *self = Self::from_bits(self.bits() | rhs.into().bits());
    }
}

#[expect(clippy::suspicious_arithmetic_impl)]
impl<T: Into<SigSet>> ops::Add<T> for SigSet {
    type Output = Self;

    fn add(self, rhs: T) -> Self {
        Self::from_bits(self.bits() | rhs.into().bits())
    }
}

#[expect(clippy::suspicious_op_assign_impl)]
impl<T: Into<SigSet>> ops::AddAssign<T> for SigSet {
    fn add_assign(&mut self, rhs: T) {
        *self = Self::from_bits(self.bits() | rhs.into().bits());
    }
}

impl<T: Into<SigSet>> ops::Sub<T> for SigSet {
    type Output = Self;

    fn sub(self, rhs: T) -> Self {
        Self::from_bits(self.bits() & !rhs.into().bits())
    }
}

impl<T: Into<SigSet>> ops::SubAssign<T> for SigSet {
    fn sub_assign(&mut self, rhs: T) {
        *self = Self::from_bits(self.bits() & !rhs.into().bits());
    }
}

impl ops::Not for SigSet {
    type Output = Self;

    fn not(self) -> Self {
        Self::from_bits(!self.bits())
    }
}

impl SigSet {
    const fn from_bits(bits: u64) -> Self {
        Self(RawCUserSigSet::new(bits))
    }

    const fn bits(self) -> u64 {
        self.0.bits()
    }

    pub(crate) fn new_empty() -> Self {
        Self::from_bits(0)
    }

    pub(crate) fn new_full() -> Self {
        Self::from_bits(!0)
    }

    #[expect(dead_code)]
    pub(crate) const fn is_empty(&self) -> bool {
        self.0.bits() == 0
    }

    #[expect(dead_code)]
    pub(crate) const fn is_full(&self) -> bool {
        self.0.bits() == !0
    }

    #[expect(dead_code)]
    pub(crate) fn count(&self) -> usize {
        self.0.bits().count_ones() as usize
    }

    pub(crate) fn contains(&self, other: impl Into<Self>) -> bool {
        let other = other.into();
        self.0.bits() & other.bits() == other.bits()
    }

    pub(crate) fn intersects(&self, other: impl Into<Self>) -> bool {
        let other = other.into();
        self.0.bits() & other.bits() != 0
    }
}

// This is to allow hexadecimally formatting a `SigSet` when debug printing it.
impl LowerHex for SigSet {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        LowerHex::fmt(&self.0.bits(), f) // delegate to u64's implementation
    }
}

/// An atomic signal mask.
///
/// This is an alias to the [`AtomicSigSet`]. All the signal in the set are
/// blocked from the delivery to a thread.
///
/// [`Relaxed`]: core::sync::atomic::Ordering::Relaxed
pub(crate) type AtomicSigMask = AtomicSigSet;

define_atomic_version_of_integer_like_type!(SigSet, {
    pub(crate) struct AtomicSigSet(AtomicU64);
});

impl From<SigSet> for AtomicSigSet {
    fn from(set: SigSet) -> Self {
        Self::new(set)
    }
}

impl AtomicSigSet {
    pub(crate) fn new_empty() -> Self {
        AtomicSigSet::new(0)
    }

    pub(crate) fn contains(&self, signals: impl Into<SigSet>, ordering: Ordering) -> bool {
        self.load(ordering).contains(signals.into())
    }
}
