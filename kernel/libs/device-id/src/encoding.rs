// SPDX-License-Identifier: MPL-2.0

//! Encodes, decodes, and validates Linux device numbers.

/// The largest major number representable by [`crate::DeviceId`].
pub(crate) const MAX_MAJOR_NUMBER: u16 = 0x0fff;

/// The largest minor number representable by [`crate::DeviceId`].
pub(crate) const MAX_MINOR_NUMBER: u32 = 0x000f_ffff;

/// Decodes the major and minor numbers from the encoded `u64` value.
///
/// See [`encode_device_numbers`] for details about how to encode a device ID to a `u64` value.
pub fn decode_device_numbers(raw: u64) -> (u32, u32) {
    let major = ((raw >> 32) & 0xffff_f000 | (raw >> 8) & 0x0000_0fff) as u32;
    let minor = ((raw >> 12) & 0xffff_ff00 | raw & 0x0000_00ff) as u32;
    (major, minor)
}

/// Encodes the major and minor numbers as a `u64` value.
///
/// The lower 32 bits use the same encoding strategy as Linux. See the Linux implementation at:
/// <https://github.com/torvalds/linux/blob/0ff41df1cb268fc69e703a08a57ee14ae967d0ca/include/linux/kdev_t.h#L39-L44>.
///
/// If the major or minor device number is too large, the additional bits will be recorded
/// using the higher 32 bits. Note that as of 2025, the Linux kernel still has no support for
/// 64-bit device IDs:
/// <https://github.com/torvalds/linux/blob/0ff41df1cb268fc69e703a08a57ee14ae967d0ca/include/linux/types.h#L18>.
/// So this encoding follows the implementation in glibc:
/// <https://github.com/bminor/glibc/blob/632d895f3e5d98162f77b9c3c1da4ec19968b671/bits/sysmacros.h#L26-L34>.
pub fn encode_device_numbers(major: u32, minor: u32) -> u64 {
    let major = major as u64;
    let minor = minor as u64;
    ((major & 0xffff_f000) << 32)
        | ((major & 0x0000_0fff) << 8)
        | ((minor & 0xffff_ff00) << 12)
        | (minor & 0x0000_00ff)
}

/// Decodes a device number if both components fit in [`crate::DeviceId`].
pub(crate) fn decode_valid_device_numbers(raw: u64) -> Option<(u16, u32)> {
    let (major, minor) = decode_device_numbers(raw);

    if major > u32::from(MAX_MAJOR_NUMBER) || minor > MAX_MINOR_NUMBER {
        return None;
    }

    Some((major as u16, minor))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_major_bits_lost_by_narrowing() {
        assert_eq!(decode_valid_device_numbers(0x0001_0000_0000_0000), None);
    }

    #[test]
    fn accepts_largest_device_number() {
        assert_eq!(
            decode_valid_device_numbers(0x0000_0000_ffff_ffff),
            Some((0x0fff, 0x000f_ffff))
        );
    }

    #[test]
    fn rejects_first_major_above_range() {
        assert_eq!(decode_valid_device_numbers(0x0000_1000_0000_0000), None);
    }

    #[test]
    fn rejects_first_minor_above_range() {
        assert_eq!(decode_valid_device_numbers(0x0000_0001_0000_0000), None);
    }
}

