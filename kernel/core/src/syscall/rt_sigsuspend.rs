// SPDX-License-Identifier: MPL-2.0

use ostd::{mm::VmIo, sync::Waiter};

use super::SyscallReturn;
use crate::{
    prelude::*,
    process::{
        posix_thread::ContextPthreadAdminApi,
        signal::sig_mask::{SigMask, validate_sigset_size},
    },
};

pub(super) fn sys_rt_sigsuspend(
    sigmask_addr: Vaddr,
    sigmask_size: usize,
    ctx: &Context,
) -> Result<SyscallReturn> {
    debug!(
        "sigmask_addr = 0x{:x}, sigmask_size = {}",
        sigmask_addr, sigmask_size
    );

    validate_sigset_size(sigmask_size)?;

    let sigmask = ctx.user_space().read_val::<SigMask>(sigmask_addr)?;
    ctx.save_and_set_sig_mask(sigmask);

    // Wait until receiving any signal
    let waiter = Waiter::new_pair().0;
    waiter.pause_until(|| None::<()>)?;

    // This syscall should always return `Err(EINTR)`. This path should never be reached.
    unreachable!("rt_sigsuspend always return EINTR");
}
