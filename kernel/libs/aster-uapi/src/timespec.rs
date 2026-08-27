// SPDX-License-Identifier: MPL-2.0

use core::{mem::offset_of, time::Duration};

use ostd_pod::{FromBytes, Immutable, IntoBytes, KnownLayout};

/// The number of nanoseconds in one normalized second.
pub const NANOSECONDS_PER_SECOND: i64 = 1_000_000_000;

/// The target-specific layout of [`CUserTimespec`].
pub const USER_TIMESPEC_LAYOUT: [usize; 4] = [
    size_of::<CUserTimespec>(),
    align_of::<CUserTimespec>(),
    offset_of!(CUserTimespec, seconds),
    offset_of!(CUserTimespec, nanoseconds),
];

/// An error returned while validating a user-provided timespec.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TimespecError {
    /// Seconds cannot be negative for a syscall duration.
    NegativeSeconds,
    /// Nanoseconds cannot be negative.
    NegativeNanoseconds,
    /// A normalized nanosecond field must be less than one second.
    NanosecondsOutOfRange,
}

/// A Linux `__kernel_timespec` copied from the syscall ABI.
///
/// Reference: <https://github.com/torvalds/linux/blob/v6.18/include/uapi/linux/time_types.h#L7-L10>.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, FromBytes, Immutable, IntoBytes, KnownLayout)]
pub struct CUserTimespec {
    seconds: i64,
    nanoseconds: i64,
}

impl CUserTimespec {
    /// Creates a timespec from its raw ABI fields.
    pub const fn new(seconds: i64, nanoseconds: i64) -> Self {
        Self {
            seconds,
            nanoseconds,
        }
    }

    /// Returns the raw seconds field.
    pub const fn seconds(self) -> i64 {
        self.seconds
    }

    /// Returns the raw nanoseconds field.
    pub const fn nanoseconds(self) -> i64 {
        self.nanoseconds
    }

    /// Validates a nonnegative normalized duration.
    pub const fn validated_duration(self) -> Result<Duration, TimespecError> {
        if self.seconds < 0 {
            return Err(TimespecError::NegativeSeconds);
        }
        if self.nanoseconds < 0 {
            return Err(TimespecError::NegativeNanoseconds);
        }
        if self.nanoseconds >= NANOSECONDS_PER_SECOND {
            return Err(TimespecError::NanosecondsOutOfRange);
        }

        Ok(Duration::new(self.seconds as u64, self.nanoseconds as u32))
    }
}

impl From<Duration> for CUserTimespec {
    fn from(duration: Duration) -> Self {
        let seconds = duration.as_secs() as i64;
        debug_assert!(seconds >= 0);
        Self::new(seconds, duration.subsec_nanos() as i64)
    }
}

#[cfg(test)]
mod tests {
    use ostd_pod::IntoBytes;

    use super::{CUserTimespec, NANOSECONDS_PER_SECOND, TimespecError, USER_TIMESPEC_LAYOUT};

    #[test]
    fn timespec_rejects_negative_and_denormalized_fields() {
        assert_eq!(
            CUserTimespec::new(-1, 0).validated_duration(),
            Err(TimespecError::NegativeSeconds)
        );
        assert_eq!(
            CUserTimespec::new(0, -1).validated_duration(),
            Err(TimespecError::NegativeNanoseconds)
        );
        assert_eq!(
            CUserTimespec::new(0, NANOSECONDS_PER_SECOND).validated_duration(),
            Err(TimespecError::NanosecondsOutOfRange)
        );
    }

    #[test]
    fn timespec_accepts_the_upper_normalized_boundary() {
        let duration = CUserTimespec::new(7, NANOSECONDS_PER_SECOND - 1)
            .validated_duration()
            .unwrap();

        assert_eq!(duration.as_secs(), 7);
        assert_eq!(duration.subsec_nanos(), 999_999_999);
    }

    #[test]
    fn user_timespec_layout_matches_the_linux_abi() {
        let timespec = CUserTimespec::new(0x0102_0304_0506_0708, 0x1112_1314_1516_1718);
        let bytes = timespec.as_bytes();

        assert_eq!(USER_TIMESPEC_LAYOUT, [16, 8, 0, 8]);
        assert_eq!(size_of::<CUserTimespec>(), 16);
        assert_eq!(align_of::<CUserTimespec>(), align_of::<i64>());
        assert_eq!(&bytes[..8], &0x0102_0304_0506_0708i64.to_ne_bytes());
        assert_eq!(&bytes[8..], &0x1112_1314_1516_1718i64.to_ne_bytes());
    }
}

#[cfg(kani)]
mod proofs {
    use super::{CUserTimespec, NANOSECONDS_PER_SECOND, TimespecError};

    #[kani::proof]
    fn proof_timespec_seconds_are_nonnegative() {
        let seconds: i64 = kani::any();
        let result = CUserTimespec::new(seconds, 0).validated_duration();

        if seconds < 0 {
            assert_eq!(result, Err(TimespecError::NegativeSeconds));
        } else {
            assert_eq!(result.unwrap().as_secs(), seconds as u64);
        }
    }

    #[kani::proof]
    fn proof_timespec_nanoseconds_are_normalized() {
        let nanoseconds: i64 = kani::any();
        let result = CUserTimespec::new(0, nanoseconds).validated_duration();

        if nanoseconds < 0 {
            assert_eq!(result, Err(TimespecError::NegativeNanoseconds));
        } else if nanoseconds >= NANOSECONDS_PER_SECOND {
            assert_eq!(result, Err(TimespecError::NanosecondsOutOfRange));
        } else {
            assert_eq!(result.unwrap().subsec_nanos(), nanoseconds as u32);
        }
    }
}
