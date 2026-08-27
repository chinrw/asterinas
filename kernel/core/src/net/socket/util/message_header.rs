// SPDX-License-Identifier: MPL-2.0

pub(crate) use aster_uapi::ControlMessageHeader as CControlHeader;

use super::{RecvFlags, SocketAddr};
use crate::{net::socket::unix::UnixControlMessage, prelude::*, util::net::CSocketOptionLevel};

/// Message header used for sendmsg/recvmsg.
#[derive(Debug)]
pub(crate) struct MessageHeader {
    pub(in crate::net) addr: Option<SocketAddr>,
    pub(in crate::net) control_messages: Vec<ControlMessage>,
}

impl MessageHeader {
    /// Creates a new `MessageHeader`.
    pub(crate) const fn new(
        addr: Option<SocketAddr>,
        control_messages: Vec<ControlMessage>,
    ) -> Self {
        Self {
            addr,
            control_messages,
        }
    }

    /// Returns the socket address.
    pub(crate) fn addr(&self) -> Option<&SocketAddr> {
        self.addr.as_ref()
    }

    /// Returns the control messages.
    pub(crate) fn control_messages(&self) -> &Vec<ControlMessage> {
        &self.control_messages
    }
}

/// Control messages in [`MessageHeader`].
#[derive(Debug)]
pub(crate) enum ControlMessage {
    Unix(UnixControlMessage),
}

impl ControlMessage {
    pub(crate) fn read_all_from(reader: &mut VmReader) -> Result<Vec<Self>> {
        // FIXME: This method may exhaust kernel memory and cause a panic if the program is
        // malicious and attempts to send too many control messages. To prevent this, we limit the
        // number of control messages, but this limit does not have a Linux equivalent.
        const MAX_NR_MSGS: usize = 32;

        let mut msgs = Vec::new();

        while reader.has_remain() && msgs.len() < MAX_NR_MSGS {
            let header = reader.read_val::<CControlHeader>()?;
            let Some(layout) = header.layout() else {
                return_errno_with_message!(
                    Errno::EINVAL,
                    "the size of the control message is invalid"
                );
            };
            let Some(step) = layout.read_step(reader.remain()) else {
                return_errno_with_message!(
                    Errno::EINVAL,
                    "the size of the control message is invalid"
                );
            };

            if let Some(msg) = Self::read_from(&header, step.payload_len(), reader)? {
                msgs.push(msg);
            }

            reader.skip(step.padding_len());
        }

        if reader.has_remain() {
            warn!("excessive control messages are currently not permitted");
            return_errno_with_message!(
                Errno::E2BIG,
                "excessive control messages are currently not permitted"
            );
        }

        Ok(msgs)
    }

    fn read_from(
        header: &CControlHeader,
        payload_len: usize,
        reader: &mut VmReader,
    ) -> Result<Option<Self>> {
        let Ok(level) = CSocketOptionLevel::try_from(header.level()) else {
            warn!("unsupported control message level in {:?}", header);
            reader.skip(payload_len);
            return Ok(None);
        };

        match level {
            CSocketOptionLevel::SOL_SOCKET => {
                // Linux manual pages say (https://man7.org/linux/man-pages/man7/unix.7.html):
                // "For historical reasons, the ancillary message types listed below are specified
                // with a SOL_SOCKET type even though they are AF_UNIX specific."
                let msg = UnixControlMessage::read_from(header, payload_len, reader)?;
                Ok(msg.map(Self::Unix))
            }
            _ => {
                warn!("unsupported control message level in {:?}", header);
                reader.skip(payload_len);
                Ok(None)
            }
        }
    }

    pub(crate) fn write_all_to(msgs: &[Self], writer: &mut VmWriter) -> (usize, RecvFlags) {
        let mut len = 0;
        let mut output_flags = RecvFlags::empty();

        for msg in msgs.iter() {
            let (header, message_flags) = match msg.write_to(writer) {
                Ok(result) => result,
                // This occurs when the buffer is too short or when some page faults cannot be
                // handled. However, at this point, there is no good way to report the errors to
                // user space. According to the Linux implementation, it seems okay to silently
                // ignore errors here.
                Err(_) => {
                    output_flags |= RecvFlags::MSG_CTRUNC;
                    break;
                }
            };
            output_flags |= message_flags;

            let Some(layout) = header.layout() else {
                output_flags |= RecvFlags::MSG_CTRUNC;
                break;
            };
            len += layout.total_len();

            let padding_len = layout.padding_len().min(writer.avail());
            writer.skip(padding_len);
            len += padding_len;
        }

        (len, output_flags)
    }

    fn write_to(&self, writer: &mut VmWriter) -> Result<(CControlHeader, RecvFlags)> {
        match self {
            Self::Unix(msg) => msg.write_to(writer),
        }
    }
}
