#![no_main]

use aster_uapi::{UserIoVec, iovec_entry_addr, truncate_iovec_len};
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    const WORDS: usize = 4;
    let word_size = size_of::<usize>();
    if data.len() < word_size * WORDS {
        return;
    }

    let base = read_usize(data, 0);
    let raw_len = read_usize(data, word_size) as isize;
    let remaining = read_usize(data, word_size * 2);
    let index = read_usize(data, word_size * 3);

    match UserIoVec::new(base, raw_len).validate() {
        Ok(iovec) => {
            assert!(raw_len >= 0);
            assert_eq!(iovec.base(), base);
            assert_eq!(iovec.len(), raw_len as usize);
        }
        Err(_) => assert!(raw_len < 0),
    }

    let len = raw_len as usize;
    let (effective_len, next_remaining) = truncate_iovec_len(len, remaining);
    assert!(effective_len <= len);
    assert!(effective_len <= remaining);
    assert_eq!(effective_len.checked_add(next_remaining), Some(remaining));

    if let Some(address) = iovec_entry_addr(base, index) {
        assert!(address >= base);
    }
});

fn read_usize(data: &[u8], offset: usize) -> usize {
    let mut bytes = [0; size_of::<usize>()];
    bytes.copy_from_slice(&data[offset..offset + size_of::<usize>()]);
    usize::from_ne_bytes(bytes)
}
