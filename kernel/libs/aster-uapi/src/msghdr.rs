// SPDX-License-Identifier: MPL-2.0

use core::mem::offset_of;

use ostd_pod::{FromBytes, Immutable, IntoBytes, KnownLayout, padding_struct};

use crate::MAX_IO_VECTOR_LENGTH;

/// The target-specific layout of [`CUserMsgHdr`].
pub const USER_MSGHDR_LAYOUT: [usize; 9] = [
    size_of::<CUserMsgHdr>(),
    align_of::<CUserMsgHdr>(),
    offset_of!(CUserMsgHdr, msg_name),
    offset_of!(CUserMsgHdr, msg_namelen),
    offset_of!(CUserMsgHdr, msg_iov),
    offset_of!(CUserMsgHdr, msg_iovlen),
    offset_of!(CUserMsgHdr, msg_control),
    offset_of!(CUserMsgHdr, msg_controllen),
    offset_of!(CUserMsgHdr, msg_flags),
];

/// An error returned while validating a user-provided message header.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MessageHeaderError {
    /// The signed socket-address length is negative.
    NegativeNameLength,
    /// The I/O vector count exceeds the Linux ABI limit.
    TooManyIovecs,
}

/// A Linux `user_msghdr` copied from the socket syscall ABI.
///
/// Reference: <https://elixir.bootlin.com/linux/v6.16/source/include/linux/socket.h#L64>.
#[padding_struct]
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, FromBytes, Immutable, IntoBytes, KnownLayout)]
pub struct CUserMsgHdr {
    msg_name: usize,
    msg_namelen: i32,
    msg_iov: usize,
    msg_iovlen: usize,
    msg_control: usize,
    msg_controllen: usize,
    msg_flags: u32,
}

impl CUserMsgHdr {
    /// Returns the user socket-address pointer.
    pub const fn name_ptr(self) -> usize {
        self.msg_name
    }

    /// Returns the raw signed socket-address length.
    pub const fn name_len(self) -> i32 {
        self.msg_namelen
    }

    /// Validates the socket-address length when its user pointer is non-null.
    pub const fn validated_name_len(self) -> Result<Option<usize>, MessageHeaderError> {
        if self.msg_name == 0 {
            return Ok(None);
        }
        if self.msg_namelen < 0 {
            return Err(MessageHeaderError::NegativeNameLength);
        }
        Ok(Some(self.msg_namelen as usize))
    }

    /// Returns the user I/O vector pointer.
    pub const fn iov_ptr(self) -> usize {
        self.msg_iov
    }

    /// Validates and returns the I/O vector count.
    pub const fn validated_iov_len(self) -> Result<usize, MessageHeaderError> {
        if self.msg_iovlen > MAX_IO_VECTOR_LENGTH {
            return Err(MessageHeaderError::TooManyIovecs);
        }
        Ok(self.msg_iovlen)
    }

    /// Returns the user ancillary-data pointer.
    pub const fn control_ptr(self) -> usize {
        self.msg_control
    }

    /// Returns the ancillary-data buffer length.
    pub const fn control_len(self) -> usize {
        self.msg_controllen
    }

    /// Replaces the socket-address length copied back to userspace.
    pub fn set_name_len(&mut self, name_len: i32) {
        self.msg_namelen = name_len;
    }

    /// Replaces the ancillary-data length copied back to userspace.
    pub fn set_control_len(&mut self, control_len: usize) {
        self.msg_controllen = control_len;
    }

    /// Replaces the message flags copied back to userspace.
    pub fn set_flags(&mut self, flags: u32) {
        self.msg_flags = flags;
    }

    #[cfg(any(kani, test))]
    fn from_raw_parts(
        msg_name: usize,
        msg_namelen: i32,
        msg_iov: usize,
        msg_iovlen: usize,
        msg_control: usize,
        msg_controllen: usize,
        msg_flags: u32,
    ) -> Self {
        Self {
            msg_name,
            msg_namelen,
            msg_iov,
            msg_iovlen,
            msg_control,
            msg_controllen,
            msg_flags,
            ..Self::default()
        }
    }
}

#[cfg(test)]
mod tests {
    use core::mem::offset_of;

    use ostd_pod::IntoBytes;

    use super::{CUserMsgHdr, MessageHeaderError, USER_MSGHDR_LAYOUT};
    use crate::MAX_IO_VECTOR_LENGTH;

    fn header(name_len: i32, iov_len: usize) -> CUserMsgHdr {
        CUserMsgHdr::from_raw_parts(0x1000, name_len, 0x2000, iov_len, 0x3000, 64, 0)
    }

    #[test]
    fn negative_message_name_length_is_rejected() {
        assert_eq!(
            header(-1, 1).validated_name_len(),
            Err(MessageHeaderError::NegativeNameLength)
        );
    }

    #[test]
    fn null_message_name_ignores_the_signed_length() {
        let header = CUserMsgHdr::from_raw_parts(0, -1, 0x2000, 1, 0x3000, 64, 0);

        assert_eq!(header.validated_name_len(), Ok(None));
    }

    #[test]
    fn message_iovec_count_obeys_the_linux_limit() {
        assert_eq!(
            header(0, MAX_IO_VECTOR_LENGTH).validated_iov_len(),
            Ok(MAX_IO_VECTOR_LENGTH)
        );
        assert_eq!(
            header(0, MAX_IO_VECTOR_LENGTH + 1).validated_iov_len(),
            Err(MessageHeaderError::TooManyIovecs)
        );
    }

    #[test]
    fn user_message_header_layout_matches_the_linux_abi() {
        let header = header(16, 1);
        let bytes = header.as_bytes();

        assert_eq!(USER_MSGHDR_LAYOUT, [56, 8, 0, 8, 16, 24, 32, 40, 48]);
        assert_eq!(size_of::<CUserMsgHdr>(), 56);
        assert_eq!(align_of::<CUserMsgHdr>(), align_of::<usize>());
        assert_eq!(offset_of!(CUserMsgHdr, msg_name), 0);
        assert_eq!(offset_of!(CUserMsgHdr, msg_namelen), 8);
        assert_eq!(offset_of!(CUserMsgHdr, msg_iov), 16);
        assert_eq!(offset_of!(CUserMsgHdr, msg_iovlen), 24);
        assert_eq!(offset_of!(CUserMsgHdr, msg_control), 32);
        assert_eq!(offset_of!(CUserMsgHdr, msg_controllen), 40);
        assert_eq!(offset_of!(CUserMsgHdr, msg_flags), 48);
        assert_eq!(bytes.len(), size_of::<CUserMsgHdr>());
        assert_eq!(&bytes[12..16], &[0; 4]);
        assert_eq!(&bytes[52..56], &[0; 4]);
    }
}

#[cfg(kani)]
mod proofs {
    use super::{CUserMsgHdr, MessageHeaderError};
    use crate::MAX_IO_VECTOR_LENGTH;

    fn header(name_ptr: usize, name_len: i32, iov_len: usize) -> CUserMsgHdr {
        CUserMsgHdr::from_raw_parts(name_ptr, name_len, 0, iov_len, 0, 0, 0)
    }

    #[kani::proof]
    fn proof_message_name_length_preserves_signedness() {
        let name_ptr: usize = kani::any();
        let name_len: i32 = kani::any();
        let result = header(name_ptr, name_len, 0).validated_name_len();

        if name_ptr == 0 {
            assert_eq!(result, Ok(None));
        } else if name_len < 0 {
            assert_eq!(result, Err(MessageHeaderError::NegativeNameLength));
        } else {
            assert_eq!(result, Ok(Some(name_len as usize)));
        }
    }

    #[kani::proof]
    fn proof_message_iovec_count_obeys_the_linux_limit() {
        let iov_len: usize = kani::any();
        let result = header(0, 0, iov_len).validated_iov_len();

        if iov_len > MAX_IO_VECTOR_LENGTH {
            assert_eq!(result, Err(MessageHeaderError::TooManyIovecs));
        } else {
            assert_eq!(result, Ok(iov_len));
        }
    }
}
