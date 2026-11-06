// SPDX-License-Identifier: MPL-2.0

#![no_std]

extern crate alloc;

pub fn allocation() -> alloc::vec::Vec<u8> {
    alloc::vec![1]
}
