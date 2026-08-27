// SPDX-License-Identifier: MPL-2.0

use aster_uapi::{
    CUserSigSet, SigsetSizeError, USER_SIGSET_LAYOUT, USER_SIGSET_SIZE, validate_sigset_size,
};
use ostd_pod::IntoBytes;

#[test]
fn sigset_size_validation_is_exact() {
    assert_eq!(validate_sigset_size(USER_SIGSET_SIZE), Ok(()));

    for size in [0, USER_SIGSET_SIZE - 1, USER_SIGSET_SIZE + 1, usize::MAX] {
        assert_eq!(
            validate_sigset_size(size),
            Err(SigsetSizeError::InvalidSize)
        );
    }
}

#[test]
fn user_sigset_layout_matches_the_linux_abi() {
    let bits = 0x0102_0304_0506_0708u64;
    let sigset = CUserSigSet::new(bits);

    assert_eq!(USER_SIGSET_SIZE, 8);
    assert_eq!(USER_SIGSET_LAYOUT, [8, 8, 0]);
    assert_eq!(sigset.bits(), bits);
    assert_eq!(sigset.as_bytes(), &bits.to_ne_bytes());
}
