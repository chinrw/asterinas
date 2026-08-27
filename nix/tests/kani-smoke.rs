// SPDX-License-Identifier: MPL-2.0

fn checked_add_one(value: u8) -> Option<u8> {
    value.checked_add(1)
}

#[kani::proof]
fn checked_add_one_never_wraps() {
    let value: u8 = kani::any();
    if let Some(result) = checked_add_one(value) {
        assert!(result > value);
    }
}
