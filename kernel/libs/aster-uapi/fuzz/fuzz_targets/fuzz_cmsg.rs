// SPDX-License-Identifier: MPL-2.0

#![no_main]

use aster_uapi::{
    CONTROL_MESSAGE_ALIGNMENT, CONTROL_MESSAGE_HEADER_LEN, ControlMessageHeader,
    ControlMessageLayout,
};
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    let word_size = size_of::<usize>();
    if data.len() < word_size * 2 {
        return;
    }

    let total_len = read_usize(data, 0);
    let available_after_header = read_usize(data, word_size);
    let header = ControlMessageHeader::from_raw_parts(total_len, 1, 2);

    if let Some(layout) = header.layout() {
        assert!(layout.total_len() >= CONTROL_MESSAGE_HEADER_LEN);
        assert!(layout.aligned_len() >= layout.total_len());
        assert_eq!(layout.aligned_len() % CONTROL_MESSAGE_ALIGNMENT, 0);
        assert!(layout.padding_len() < CONTROL_MESSAGE_ALIGNMENT);

        if let Some(step) = layout.read_step(available_after_header) {
            assert!(step.payload_len() <= available_after_header);
            assert!(step.consumed_after_header() <= available_after_header);
            assert!(step.padding_len() <= layout.padding_len());
        }
    }

    if let Some(payload_len) = ControlMessageLayout::payload_capacity(total_len)
        && let Some(generated) = ControlMessageHeader::new(1, 2, payload_len)
    {
        assert_eq!(generated.total_len(), total_len);
        assert_eq!(generated.layout().unwrap().payload_len(), payload_len);
    }
});

fn read_usize(data: &[u8], offset: usize) -> usize {
    let mut bytes = [0; size_of::<usize>()];
    bytes.copy_from_slice(&data[offset..offset + size_of::<usize>()]);
    usize::from_ne_bytes(bytes)
}
