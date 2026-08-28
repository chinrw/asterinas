// SPDX-License-Identifier: MPL-2.0

//! Encodes, decodes, and validates Linux device numbers.

#![cfg_attr(kani, allow(unexpected_cfgs))]

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

#[cfg(kani)]
mod proofs {
    use super::*;

    #[kani::proof]
    fn raw_round_trip() {
        let raw: u64 = kani::any();
        let (major, minor) = decode_device_numbers(raw);

        assert_eq!(encode_device_numbers(major, minor), raw);
    }

    #[kani::proof]
    fn pair_round_trip() {
        let major: u32 = kani::any();
        let minor: u32 = kani::any();

        assert_eq!(
            decode_device_numbers(encode_device_numbers(major, minor)),
            (major, minor)
        );
    }

    #[kani::proof]
    fn validated_decode_acceptance_is_exact() {
        let raw: u64 = kani::any();
        let (major, minor) = decode_device_numbers(raw);
        let is_in_range = major <= u32::from(MAX_MAJOR_NUMBER) && minor <= MAX_MINOR_NUMBER;

        assert_eq!(decode_valid_device_numbers(raw).is_some(), is_in_range);
    }

    #[kani::proof]
    fn validated_decode_preserves_decoded_values() {
        let raw: u64 = kani::any();
        let (decoded_major, decoded_minor) = decode_device_numbers(raw);

        if let Some((major, minor)) = decode_valid_device_numbers(raw) {
            assert_eq!(u32::from(major), decoded_major);
            assert_eq!(minor, decoded_minor);
        }
    }

    #[kani::proof]
    fn encoded_pairs_are_classified_exactly() {
        let major: u32 = kani::any();
        let minor: u32 = kani::any();
        let raw = encode_device_numbers(major, minor);
        let expected = if major <= u32::from(MAX_MAJOR_NUMBER) && minor <= MAX_MINOR_NUMBER {
            Some((major as u16, minor))
        } else {
            None
        };

        assert_eq!(decode_valid_device_numbers(raw), expected);
    }

    // The two proofs below restate the Linux `dev_t` specification with
    // literals on purpose. Referencing the production constants (or the
    // production decoder, where avoidable) would let a wrong value slip
    // into the specification and the implementation at the same time,
    // leaving the proof green.

    #[kani::proof]
    fn documented_limits_are_enforced() {
        let raw: u64 = kani::any();
        let (major, minor) = decode_device_numbers(raw);
        // Linux limits a major to 12 bits and a minor to 20 bits.
        let fits_linux_dev_t = major <= 0x0fff && minor <= 0x000f_ffff;

        assert_eq!(decode_valid_device_numbers(raw).is_some(), fits_linux_dev_t);
    }

    #[kani::proof]
    fn encoded_fields_match_linux_layout() {
        let major: u32 = kani::any();
        let minor: u32 = kani::any();
        let raw = encode_device_numbers(major, minor);

        // The glibc 64-bit layout, field by field. Together with
        // `raw_round_trip` this also pins the decoder: the four fields
        // cover all 64 bits, so the encoding is a bit permutation and
        // the decoder is forced to be its unique inverse.
        assert_eq!(raw & 0xff, u64::from(minor & 0xff));
        assert_eq!(raw >> 8 & 0x0fff, u64::from(major & 0x0fff));
        assert_eq!(raw >> 20 & 0x00ff_ffff, u64::from(minor >> 8));
        assert_eq!(raw >> 44, u64::from(major >> 12));
    }
}
