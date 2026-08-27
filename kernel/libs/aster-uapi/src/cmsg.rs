// SPDX-License-Identifier: MPL-2.0

use core::mem::offset_of;

use ostd_pod::{FromBytes, Immutable, IntoBytes, KnownLayout};

/// Alignment required between Linux ancillary-data records.
///
/// Reference: <https://elixir.bootlin.com/linux/v6.16/source/include/linux/socket.h#L103>.
pub const CONTROL_MESSAGE_ALIGNMENT: usize = size_of::<usize>();

/// Size of a Linux `cmsghdr` for the current pointer width.
pub const CONTROL_MESSAGE_HEADER_LEN: usize = size_of::<ControlMessageHeader>();

/// The target-specific layout of [`ControlMessageHeader`].
pub const CONTROL_MESSAGE_HEADER_LAYOUT: [usize; 5] = [
    size_of::<ControlMessageHeader>(),
    align_of::<ControlMessageHeader>(),
    offset_of!(ControlMessageHeader, len),
    offset_of!(ControlMessageHeader, level),
    offset_of!(ControlMessageHeader, type_),
];

/// A Linux `cmsghdr` copied from the syscall ABI.
#[repr(C)]
#[derive(Clone, Copy, Debug, Eq, FromBytes, Immutable, IntoBytes, KnownLayout, PartialEq)]
pub struct ControlMessageHeader {
    len: usize,
    level: i32,
    type_: i32,
}

impl ControlMessageHeader {
    /// Creates a header when the payload and alignment calculations do not overflow.
    pub const fn new(level: i32, type_: i32, payload_len: usize) -> Option<Self> {
        let Some(layout) = ControlMessageLayout::from_payload_len(payload_len) else {
            return None;
        };
        Some(Self {
            len: layout.total_len,
            level,
            type_,
        })
    }

    /// Creates an unvalidated header from raw ABI fields.
    pub const fn from_raw_parts(len: usize, level: i32, type_: i32) -> Self {
        Self { len, level, type_ }
    }

    /// Validates the raw total length and computes its aligned layout.
    pub const fn layout(self) -> Option<ControlMessageLayout> {
        ControlMessageLayout::from_total_len(self.len)
    }

    /// Returns the raw total length, including the header and excluding padding.
    pub const fn total_len(self) -> usize {
        self.len
    }

    /// Returns the originating protocol value.
    pub const fn level(self) -> i32 {
        self.level
    }

    /// Returns the protocol-specific type value.
    pub const fn type_(self) -> i32 {
        self.type_
    }
}

/// Validated sizes for one Linux ancillary-data record.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ControlMessageLayout {
    total_len: usize,
    payload_len: usize,
    aligned_len: usize,
}

impl ControlMessageLayout {
    /// Computes a layout from a user-provided total length.
    pub const fn from_total_len(total_len: usize) -> Option<Self> {
        let Some(payload_len) = total_len.checked_sub(CONTROL_MESSAGE_HEADER_LEN) else {
            return None;
        };
        let Some(aligned_len) = checked_align_up(total_len, CONTROL_MESSAGE_ALIGNMENT) else {
            return None;
        };
        Some(Self {
            total_len,
            payload_len,
            aligned_len,
        })
    }

    /// Computes a layout from a payload length produced by the kernel.
    pub const fn from_payload_len(payload_len: usize) -> Option<Self> {
        let Some(total_len) = CONTROL_MESSAGE_HEADER_LEN.checked_add(payload_len) else {
            return None;
        };
        Self::from_total_len(total_len)
    }

    /// Returns the largest payload that fits in a total buffer length.
    pub const fn payload_capacity(available: usize) -> Option<usize> {
        available.checked_sub(CONTROL_MESSAGE_HEADER_LEN)
    }

    /// Validates the available bytes after the header and returns one parser step.
    pub const fn read_step(self, available_after_header: usize) -> Option<ControlMessageReadStep> {
        let Some(available_after_payload) = available_after_header.checked_sub(self.payload_len)
        else {
            return None;
        };
        let padding_len = min_usize(self.padding_len(), available_after_payload);
        Some(ControlMessageReadStep {
            payload_len: self.payload_len,
            padding_len,
        })
    }

    /// Returns the total length, including the header and excluding padding.
    pub const fn total_len(self) -> usize {
        self.total_len
    }

    /// Returns the payload length.
    pub const fn payload_len(self) -> usize {
        self.payload_len
    }

    /// Returns the aligned length, including trailing padding.
    pub const fn aligned_len(self) -> usize {
        self.aligned_len
    }

    /// Returns the trailing padding length.
    pub const fn padding_len(self) -> usize {
        self.aligned_len - self.total_len
    }
}

/// The payload and padding consumed after a validated control-message header.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ControlMessageReadStep {
    payload_len: usize,
    padding_len: usize,
}

impl ControlMessageReadStep {
    /// Returns the payload bytes consumed by this step.
    pub const fn payload_len(self) -> usize {
        self.payload_len
    }

    /// Returns the available padding bytes consumed by this step.
    pub const fn padding_len(self) -> usize {
        self.padding_len
    }

    /// Returns all bytes consumed after the already-read header.
    pub const fn consumed_after_header(self) -> usize {
        self.payload_len + self.padding_len
    }
}

const fn checked_align_up(value: usize, alignment: usize) -> Option<usize> {
    let mask = alignment - 1;
    let Some(rounded) = value.checked_add(mask) else {
        return None;
    };
    Some(rounded & !mask)
}

const fn min_usize(left: usize, right: usize) -> usize {
    if left < right { left } else { right }
}

#[cfg(test)]
mod tests {
    use core::mem::offset_of;

    use ostd_pod::IntoBytes;

    use super::{CONTROL_MESSAGE_ALIGNMENT, CONTROL_MESSAGE_HEADER_LEN, ControlMessageHeader};

    #[test]
    fn control_message_header_layout_matches_the_linux_abi() {
        assert_eq!(size_of::<ControlMessageHeader>(), size_of::<usize>() + 8);
        assert_eq!(align_of::<ControlMessageHeader>(), align_of::<usize>());
        assert_eq!(offset_of!(ControlMessageHeader, len), 0);
        assert_eq!(offset_of!(ControlMessageHeader, level), size_of::<usize>());
        assert_eq!(
            offset_of!(ControlMessageHeader, type_),
            size_of::<usize>() + size_of::<i32>()
        );
    }

    #[test]
    fn control_message_layout_round_trips_payload_length() {
        let header = ControlMessageHeader::new(1, 2, 5).unwrap();
        let layout = header.layout().unwrap();
        let expected_padding = (CONTROL_MESSAGE_ALIGNMENT
            - layout.total_len() % CONTROL_MESSAGE_ALIGNMENT)
            % CONTROL_MESSAGE_ALIGNMENT;

        assert_eq!(layout.payload_len(), 5);
        assert_eq!(layout.padding_len(), expected_padding);
        assert_eq!(
            layout.aligned_len(),
            CONTROL_MESSAGE_HEADER_LEN + 5 + expected_padding
        );
    }

    #[test]
    fn control_message_header_bytes_are_fully_initialized() {
        let level = 0x0102_0304i32;
        let type_ = 0x0506_0708i32;
        let header = ControlMessageHeader::new(level, type_, 0).unwrap();
        let bytes = header.as_bytes();

        assert_eq!(
            &bytes[..size_of::<usize>()],
            &CONTROL_MESSAGE_HEADER_LEN.to_ne_bytes()
        );
        assert_eq!(
            &bytes[size_of::<usize>()..size_of::<usize>() + size_of::<i32>()],
            &level.to_ne_bytes()
        );
        assert_eq!(
            &bytes[size_of::<usize>() + size_of::<i32>()..],
            &type_.to_ne_bytes()
        );
    }

    #[test]
    fn invalid_control_message_lengths_are_rejected() {
        let header = ControlMessageHeader::from_raw_parts(1, 1, 2);

        assert_eq!(header.layout(), None);
        assert_eq!(ControlMessageHeader::new(1, 2, usize::MAX), None);
    }
}

#[cfg(kani)]
mod proofs {
    use super::{
        CONTROL_MESSAGE_ALIGNMENT, CONTROL_MESSAGE_HEADER_LEN, ControlMessageHeader,
        ControlMessageLayout,
    };

    #[kani::proof]
    fn proof_control_message_alignment_does_not_wrap() {
        let total_len: usize = kani::any();

        if let Some(layout) = ControlMessageLayout::from_total_len(total_len) {
            assert!(layout.total_len() >= CONTROL_MESSAGE_HEADER_LEN);
            assert!(layout.aligned_len() >= layout.total_len());
            assert_eq!(layout.aligned_len() % CONTROL_MESSAGE_ALIGNMENT, 0);
            assert!(layout.padding_len() < CONTROL_MESSAGE_ALIGNMENT);
            assert_eq!(
                layout.total_len().checked_add(layout.padding_len()),
                Some(layout.aligned_len())
            );
        }
    }

    #[kani::proof]
    fn proof_control_message_parser_step_makes_progress() {
        let total_len: usize = kani::any();
        let available_after_header: usize = kani::any();
        let header = ControlMessageHeader::from_raw_parts(total_len, kani::any(), kani::any());

        if let Some(layout) = header.layout()
            && let Some(step) = layout.read_step(available_after_header)
        {
            assert!(CONTROL_MESSAGE_HEADER_LEN > 0);
            assert!(step.payload_len() <= available_after_header);
            assert!(step.consumed_after_header() <= available_after_header);
            assert!(step.padding_len() <= layout.padding_len());
        }
    }

    #[kani::proof]
    fn proof_control_message_payload_round_trip() {
        let payload_len: usize = kani::any();

        if let Some(header) = ControlMessageHeader::new(kani::any(), kani::any(), payload_len) {
            let layout = header.layout().unwrap();
            assert_eq!(layout.payload_len(), payload_len);
        }
    }
}
