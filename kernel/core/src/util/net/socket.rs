// SPDX-License-Identifier: MPL-2.0

use aster_uapi::CUserMsgHdr as RawCUserMsgHdr;

use super::read_socket_addr_from_user;
use crate::{
    net::socket::util::{ControlMessage, RecvFlags, SocketAddr},
    prelude::*,
    util::{VmReaderArray, VmWriterArray, net::write_socket_addr_with_max_len},
};

/// Standard well-defined IP protocols.
/// From <https://elixir.bootlin.com/linux/v6.0.9/source/include/uapi/linux/in.h>.
#[expect(non_camel_case_types)]
#[repr(i32)]
#[derive(Clone, Copy, Debug, TryFromInt)]
pub(crate) enum Protocol {
    IPPROTO_IP = 0,         /* Dummy protocol for TCP		*/
    IPPROTO_ICMP = 1,       /* Internet Control Message Protocol	*/
    IPPROTO_IGMP = 2,       /* Internet Group Management Protocol	*/
    IPPROTO_TCP = 6,        /* Transmission Control Protocol	*/
    IPPROTO_EGP = 8,        /* Exterior Gateway Protocol		*/
    IPPROTO_PUP = 12,       /* PUP protocol				*/
    IPPROTO_UDP = 17,       /* User Datagram Protocol		*/
    IPPROTO_IDP = 22,       /* XNS IDP protocol			*/
    IPPROTO_TP = 29,        /* SO Transport Protocol Class 4	*/
    IPPROTO_DCCP = 33,      /* Datagram Congestion Control Protocol */
    IPPROTO_IPV6 = 41,      /* IPv6-in-IPv4 tunnelling		*/
    IPPROTO_RSVP = 46,      /* RSVP Protocol			*/
    IPPROTO_GRE = 47,       /* Cisco GRE tunnels (rfc 1701,1702)	*/
    IPPROTO_ESP = 50,       /* Encapsulation Security Payload protocol */
    IPPROTO_AH = 51,        /* Authentication Header protocol	*/
    IPPROTO_MTP = 92,       /* Multicast Transport Protocol		*/
    IPPROTO_BEETPH = 94,    /* IP option pseudo header for BEET	*/
    IPPROTO_ENCAP = 98,     /* Encapsulation Header			*/
    IPPROTO_PIM = 103,      /* Protocol Independent Multicast	*/
    IPPROTO_COMP = 108,     /* Compression Header Protocol		*/
    IPPROTO_SCTP = 132,     /* Stream Control Transport Protocol	*/
    IPPROTO_UDPLITE = 136,  /* UDP-Lite (RFC 3828)			*/
    IPPROTO_MPLS = 137,     /* MPLS in IP (RFC 4023)		*/
    IPPROTO_ETHERNET = 143, /* Ethernet-within-IPv6 Encapsulation	*/
    IPPROTO_RAW = 255,      /* Raw IP packets			*/
    IPPROTO_MPTCP = 262,    /* Multipath TCP connection		*/
}

/// Socket types.
/// From <https://elixir.bootlin.com/linux/v6.0.9/source/include/linux/net.h>.
#[expect(non_camel_case_types)]
#[repr(i32)]
#[derive(Clone, Copy, Debug, Eq, PartialEq, TryFromInt)]
pub(crate) enum SockType {
    /// Stream socket
    SOCK_STREAM = 1,
    /// Datagram socket
    SOCK_DGRAM = 2,
    /// Raw socket
    SOCK_RAW = 3,
    /// Reliably-delivered message
    SOCK_RDM = 4,
    /// Sequential packet socket
    SOCK_SEQPACKET = 5,
    /// Datagram Congestion Control Protocol socket
    SOCK_DCCP = 6,
    /// Linux specific way of getting packets at the dev level
    SOCK_PACKET = 10,
}

pub(crate) const SOCK_TYPE_MASK: i32 = 0xf;

bitflags! {
    #[repr(C)]
    #[derive(Pod)]
    pub(crate) struct SockFlags: i32 {
        const SOCK_NONBLOCK = 1 << 11;
        const SOCK_CLOEXEC = 1 << 19;
    }
}

#[repr(transparent)]
#[derive(Clone, Copy, Debug, Pod)]
pub(crate) struct CUserMsgHdr(RawCUserMsgHdr);

impl CUserMsgHdr {
    pub(crate) fn read_socket_addr_from_user(&self) -> Result<Option<SocketAddr>> {
        let Some(name_len) = self.validated_name_len()? else {
            return Ok(None);
        };
        let socket_addr = read_socket_addr_from_user(self.0.name_ptr(), name_len)?;
        Ok(Some(socket_addr))
    }

    pub(crate) fn write_socket_addr_to_user(&self, addr: Option<&SocketAddr>) -> Result<i32> {
        if self.0.name_ptr() == 0 {
            // The length field will not be touched if the name pointer is NULL.
            // See <https://elixir.bootlin.com/linux/v6.15.6/source/net/socket.c#L2792>.
            return Ok(self.0.name_len());
        }

        let actual_len = if let Some(addr) = addr {
            write_socket_addr_with_max_len(addr, self.0.name_ptr(), self.0.name_len())?
        } else {
            0
        };
        Ok(actual_len)
    }

    pub(crate) fn read_control_messages_from_user(
        &self,
        user_space: &CurrentUserSpace,
    ) -> Result<Vec<ControlMessage>> {
        if self.0.control_ptr() == 0 {
            return Ok(Vec::new());
        }

        let mut reader = user_space.reader(self.0.control_ptr(), self.0.control_len())?;
        let control_messages = ControlMessage::read_all_from(&mut reader)?;
        Ok(control_messages)
    }

    pub(crate) fn write_control_messages_to_user(
        &self,
        control_messages: &[ControlMessage],
        user_space: &CurrentUserSpace,
    ) -> Result<(u32, RecvFlags)> {
        if self.0.control_ptr() == 0 {
            // The length field will be set even if the control message pointer is NULL.
            // See <https://elixir.bootlin.com/linux/v6.15.6/source/net/socket.c#L2807>.
            let output_flags = if control_messages.is_empty() {
                RecvFlags::empty()
            } else {
                RecvFlags::MSG_CTRUNC
            };
            return Ok((0, output_flags));
        }

        let mut writer = user_space.writer(self.0.control_ptr(), self.0.control_len())?;
        let (write_len, output_flags) = ControlMessage::write_all_to(control_messages, &mut writer);
        Ok((write_len as u32, output_flags))
    }

    pub(crate) fn copy_reader_array_from_user<'a>(
        &self,
        user_space: &'a CurrentUserSpace<'a>,
    ) -> Result<VmReaderArray<'a>> {
        let iov_len = self.validated_iov_len()?;
        VmReaderArray::from_user_io_vecs(user_space, self.0.iov_ptr(), iov_len)
    }

    pub(crate) fn copy_writer_array_from_user<'a>(
        &self,
        user_space: &'a CurrentUserSpace<'a>,
    ) -> Result<VmWriterArray<'a>> {
        self.validated_name_len()?;
        let iov_len = self.validated_iov_len()?;
        VmWriterArray::from_user_io_vecs(user_space, self.0.iov_ptr(), iov_len)
    }

    pub(crate) fn set_name_len(&mut self, name_len: i32) {
        self.0.set_name_len(name_len);
    }

    pub(crate) fn set_control_len(&mut self, control_len: usize) {
        self.0.set_control_len(control_len);
    }

    pub(crate) fn set_flags(&mut self, flags: u32) {
        self.0.set_flags(flags);
    }

    fn validated_name_len(&self) -> Result<Option<usize>> {
        self.0.validated_name_len().map_err(|_| {
            Error::with_message(
                Errno::EINVAL,
                "the socket address length cannot be negative",
            )
        })
    }

    fn validated_iov_len(&self) -> Result<usize> {
        self.0.validated_iov_len().map_err(|_| {
            Error::with_message(Errno::EMSGSIZE, "the I/O vector contains too many buffers")
        })
    }
}
