// SPDX-License-Identifier: MPL-2.0

//! A module represents Linux kernel device IDs.
//!
//! In Linux, each device is identified by a **major** number and a **minor** number.
//! These two numbers together form a unique identifier for the device, which is used
//! in device files (e.g., `/dev/sda`, `/dev/null`) and system calls like `mknod`.
//!
//! For more information about device ID allocation and usage in Linux, see:
//! <https://www.kernel.org/doc/Documentation/admin-guide/devices.txt>

#![no_std]
#![deny(unsafe_code)]

mod encoding;

use aster_util::ranged_integer::{RangedU16, RangedU32};

pub use self::encoding::{decode_device_numbers, encode_device_numbers};

/// A device ID, embedding the major ID and minor ID.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, PartialOrd, Ord)]
pub struct DeviceId(u32);

impl DeviceId {
    /// Creates a device ID from the major device number and the minor device number.
    pub fn new(major: MajorId, minor: MinorId) -> Self {
        Self(((major.get() as u32) << 20) | minor.get())
    }

    /// Creates a null device ID (both major and minor are 0).
    pub const fn null() -> Self {
        Self(0)
    }

    /// Returns the encoded `u32` value.
    pub fn to_raw(&self) -> u32 {
        self.0
    }

    /// Returns the major device number.
    pub fn major(&self) -> MajorId {
        MajorId::new((self.0 >> 20) as u16)
    }

    /// Returns the minor device number.
    pub fn minor(&self) -> MinorId {
        MinorId::new(self.0 & 0xf_ffff)
    }

    /// Returns whether the device ID refers to a null device.
    ///
    /// A null device takes the value of 0 for both the major and minor IDs.
    pub fn is_null(self) -> bool {
        self.0 == 0
    }

    /// Returns whether the device ID refers to an anonymous or unnamed device.
    ///
    /// An anonymous device has the value of 0 as the major ID but a non-zero value for the minor ID.
    pub fn is_anonymous(self) -> bool {
        self.major().get() == 0 && self.minor().get() >= 1
    }
}

impl DeviceId {
    /// Creates a device ID from the encoded `u64` value.
    ///
    /// Returns `None` if the major or minor device number is not falling in the valid range.
    pub fn from_encoded_u64(raw: u64) -> Option<Self> {
        let (major, minor) = encoding::decode_valid_device_numbers(raw)?;
        let major = MajorId::try_from(major).ok()?;
        let minor = MinorId::try_from(minor).ok()?;
        Some(Self::new(major, minor))
    }

    /// Encodes the device ID as a `u64` value.
    pub fn as_encoded_u64(&self) -> u64 {
        encode_device_numbers(self.major().get() as u32, self.minor().get())
    }
}

const MAX_MAJOR_ID: u16 = encoding::MAX_MAJOR_NUMBER;

const MAX_MINOR_ID: u32 = encoding::MAX_MINOR_NUMBER;

/// The major component of a device ID.
///
/// A major ID is a 12-bit integer, thus falling in the range of `0..(1u16 << 12)`.
///
/// The major ID 0 is reserved to represent an invalid device (when paired with a minor ID of 0)
/// or an anonymous device (used for pseudo filesystems).
///
/// Reference: <https://elixir.bootlin.com/linux/v6.13/source/include/linux/kdev_t.h#L10>.
pub type MajorId = RangedU16<0, MAX_MAJOR_ID>;

/// The minor component of a device ID.
///
/// A minor ID is a 20-bit integer, thus falling in the range of `0..(1u32 << 20)`.
///
/// Reference: <https://elixir.bootlin.com/linux/v6.13/source/include/linux/kdev_t.h#L11>.
pub type MinorId = RangedU32<0, MAX_MINOR_ID>;
