// SPDX-License-Identifier: MPL-2.0

use aster_uapi::{CheckedAddressRange, checked_page_align};

use super::SyscallReturn;
use crate::{
    prelude::*,
    vm::vmar::{RemapOldMappingAction, VMAR_CAP_ADDR, VMAR_LOWEST_ADDR},
};

pub(super) fn sys_mremap(
    old_addr: Vaddr,
    old_size: usize,
    new_size: usize,
    flags: i32,
    new_addr: Vaddr,
    ctx: &Context,
) -> Result<SyscallReturn> {
    let flags = MremapFlags::from_bits(flags)
        .ok_or_else(|| Error::with_message(Errno::EINVAL, "invalid flags"))?;
    let new_addr = do_sys_mremap(old_addr, old_size, new_size, flags, new_addr, ctx)?;
    Ok(SyscallReturn::Return(new_addr as _))
}

fn do_sys_mremap(
    old_addr: Vaddr,
    old_size: usize,
    new_size: usize,
    flags: MremapFlags,
    new_addr: Vaddr,
    ctx: &Context,
) -> Result<Vaddr> {
    debug!(
        "old_addr = 0x{:x}, old_size = {}, new_size = {}, flags = {:?}, new_addr = 0x{:x}",
        old_addr, old_size, new_size, flags, new_addr,
    );

    if !old_addr.is_multiple_of(PAGE_SIZE) {
        return_errno_with_message!(Errno::EINVAL, "old_addr must be page-aligned");
    }
    if new_size == 0 {
        return_errno_with_message!(Errno::EINVAL, "new_size cannot be zero");
    }
    if old_size == 0 {
        return_errno_with_message!(Errno::EINVAL, "copying shareable mapping is not supported");
    }

    let Some(old_size) = checked_page_align(old_size, PAGE_SIZE) else {
        return_errno_with_message!(Errno::EINVAL, "the old size overflows");
    };
    let Some(new_size) = checked_page_align(new_size, PAGE_SIZE) else {
        return_errno_with_message!(Errno::EINVAL, "the new size overflows");
    };
    let Some(old_range) = CheckedAddressRange::new(old_addr, old_size, VMAR_CAP_ADDR) else {
        return_errno_with_message!(Errno::EINVAL, "the old address range is not in userspace");
    };

    // `MREMAP_DONTUNMAP` keeps the old VMA in the tree as an anonymous
    // zero-fill-on-demand mapping; we must move pages even when the sizes
    // are equal.  `MREMAP_MAYMOVE` is required because the mapping cannot
    // stay at its current address (the physical pages must be relocated).
    if flags.contains(MremapFlags::MREMAP_DONTUNMAP) {
        if !flags.contains(MremapFlags::MREMAP_MAYMOVE) {
            return_errno_with_message!(
                Errno::EINVAL,
                "MREMAP_DONTUNMAP must be combined with MREMAP_MAYMOVE"
            );
        }
        if new_size != old_size {
            return_errno_with_message!(
                Errno::EINVAL,
                "MREMAP_DONTUNMAP requires new_size to equal old_size"
            );
        }
    }

    if flags.contains(MremapFlags::MREMAP_FIXED) {
        if !flags.contains(MremapFlags::MREMAP_MAYMOVE) {
            return_errno_with_message!(
                Errno::EINVAL,
                "MREMAP_FIXED specified without also specifying MREMAP_MAYMOVE"
            );
        }
        if !new_addr.is_multiple_of(PAGE_SIZE) || new_addr < VMAR_LOWEST_ADDR {
            return_errno_with_message!(Errno::EINVAL, "the new address is not valid");
        }
        let Some(new_range) = CheckedAddressRange::new(new_addr, new_size, VMAR_CAP_ADDR) else {
            return_errno_with_message!(Errno::EINVAL, "the new address range is not in userspace");
        };
        if old_range.overlaps(new_range) {
            return_errno_with_message!(Errno::EINVAL, "the old and new address ranges overlap");
        }
    }

    let action = if flags.contains(MremapFlags::MREMAP_DONTUNMAP) {
        RemapOldMappingAction::Keep
    } else {
        RemapOldMappingAction::Unmap
    };

    let user_space = ctx.user_space();
    let vmar = user_space.vmar();

    // When `MREMAP_DONTUNMAP` is set, we must move the mapping rather than
    // shrinking in place, even though `new_size` == `old_size`.
    if !flags.contains(MremapFlags::MREMAP_FIXED)
        && new_size <= old_size
        && action == RemapOldMappingAction::Unmap
    {
        // We can shrink a old range which spans multiple mappings. See
        // <https://github.com/google/gvisor/blob/95d875276806484f974ce9e95556a561331f8e22/test/syscalls/linux/mremap.cc#L100-L117>.
        vmar.resize_mapping(old_addr, old_size, new_size, false)?;
        return Ok(old_addr);
    }

    if flags.contains(MremapFlags::MREMAP_MAYMOVE) {
        if flags.contains(MremapFlags::MREMAP_FIXED) {
            vmar.remap(old_addr, old_size, Some(new_addr), new_size, action)
        } else {
            vmar.remap(old_addr, old_size, None, new_size, action)
        }
    } else {
        // We can ensure that `new_size > old_size` here. Since we are enlarging
        // the old mapping, it is necessary to check whether the old range lies
        // in a single mapping.
        vmar.resize_mapping(old_addr, old_size, new_size, true)?;
        Ok(old_addr)
    }
}

bitflags! {
    struct MremapFlags: i32 {
        const MREMAP_MAYMOVE = 1 << 0;
        const MREMAP_FIXED = 1 << 1;
        const MREMAP_DONTUNMAP = 1 << 2;
    }
}
