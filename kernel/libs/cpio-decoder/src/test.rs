// SPDX-License-Identifier: MPL-2.0

use lending_iterator::LendingIterator;

use super::{CpioDecoder, FileType, error::*};

#[test]
fn decoder() {
    use std::process::{Command, Stdio};

    let manifest_path = std::env::var("CARGO_MANIFEST_DIR").unwrap();
    let manifest_path = std::path::Path::new(manifest_path.as_str());

    // Prepare the cpio buffer
    let buffer = {
        let mut find_process = Command::new("find")
            .arg(manifest_path.as_os_str())
            .stdout(Stdio::piped())
            .spawn()
            .expect("find command is not started");
        let ecode = find_process.wait().expect("failed to execute find");
        assert!(ecode.success());
        let find_stdout = find_process.stdout.take().unwrap();
        let output = Command::new("cpio")
            .stdin(find_stdout)
            .args(["-o", "-H", "newc"])
            .output()
            .expect("failed to execute cpio");
        assert!(output.status.success());
        output.stdout
    };

    let mut decoder = CpioDecoder::new(buffer.as_slice());
    // 1st entry must be the root entry
    let entry = {
        let entry_result = decoder.next().unwrap();
        entry_result.unwrap()
    };
    assert_eq!(entry.name(), manifest_path.as_os_str());
    assert!(entry.metadata().file_type() == FileType::Dir);
    assert!(entry.metadata().ino() > 0);

    // Other entries
    while let Some(decode_result) = decoder.next() {
        let mut entry = decode_result.unwrap();
        assert!(entry.metadata().ino() > 0);
        if entry.name() == manifest_path.join("src").as_os_str() {
            assert!(entry.metadata().file_type() == FileType::Dir);
            assert!(entry.metadata().ino() > 0);
        } else if entry.name() == manifest_path.join("src").join("lib.rs").as_os_str()
            || entry.name() == manifest_path.join("src").join("test.rs").as_os_str()
            || entry.name() == manifest_path.join("src").join("error.rs").as_os_str()
            || entry.name() == manifest_path.join("Cargo.toml").as_os_str()
        {
            assert!(entry.metadata().file_type() == FileType::File);
            assert!(entry.metadata().size() > 0);
            let mut buffer: Vec<u8> = Vec::new();
            assert!(entry.read_all(&mut buffer).is_ok());
        } else {
            panic!("unexpected entry: {:?}", entry.name());
        }
    }
}

/// Builds a single-entry archive: a 110-byte header for a regular file
/// named "a", with the field at byte offset `offset` (if any) overwritten.
fn single_entry_with_field(offset: usize, field: &str) -> Vec<u8> {
    let mut buffer = Vec::new();
    buffer.extend_from_slice(b"070701"); // magic
    for _ in 0..13 {
        buffer.extend_from_slice(b"00000000");
    }
    buffer[14..22].copy_from_slice(b"00008000"); // mode: regular file
    buffer[94..102].copy_from_slice(b"00000002"); // name_size: "a\0"
    if offset > 0 {
        buffer[offset..offset + 8].copy_from_slice(field.as_bytes());
    }
    buffer.extend_from_slice(b"a\0"); // header + name = 112, already 4-aligned
    buffer
}

#[test]
fn accepts_handcrafted_regular_file_entry() {
    let buffer = single_entry_with_field(0, "");
    let mut decoder = CpioDecoder::new(buffer.as_slice());
    let entry = decoder.next().unwrap().unwrap();
    assert_eq!(entry.name(), "a");
    assert_eq!(entry.metadata().file_type(), FileType::File);
}

#[test]
fn rejects_plus_sign_in_hex_field() {
    // `u32::from_str_radix` alone would accept "+0000001".
    let buffer = single_entry_with_field(6, "+0000001"); // ino field
    let mut decoder = CpioDecoder::new(buffer.as_slice());
    let entry_result = decoder.next().unwrap();
    assert_eq!(entry_result.err(), Some(Error::ParseIntError));
}

#[test]
fn rejects_oversized_name_size_without_allocating() {
    let buffer = single_entry_with_field(94, "ffffffff"); // name_size field
    let mut decoder = CpioDecoder::new(buffer.as_slice());
    let entry_result = decoder.next().unwrap();
    assert_eq!(entry_result.err(), Some(Error::FileNameError));
}

#[test]
fn align_up_pad_covers_full_input_domain() {
    use super::align_up_pad;

    assert_eq!(align_up_pad(0, 4), 0);
    assert_eq!(align_up_pad(1, 4), 3);
    assert_eq!(align_up_pad(4, 4), 0);
    assert_eq!(align_up_pad(110 + 2, 4), 0);
    // The old `align_up(size, 4) - size` overflowed here.
    assert_eq!(align_up_pad(usize::MAX, 4), 1);
}

#[test]
fn short_buffer() {
    let short_buffer: Vec<u8> = Vec::new();
    let mut decoder = CpioDecoder::new(short_buffer.as_slice());
    let entry_result = decoder.next().unwrap();
    assert!(entry_result.is_err());
    assert!(entry_result.err() == Some(Error::BufferShortError));
}

#[test]
fn invalid_buffer() {
    let buffer: &[u8] = b"invalidmagic.invalidmagic.invalidmagic.invalidmagic.invalidmagic.invalidmagic.invalidmagic.invalidmagic.invalidmagic.invalidmagic";
    let mut decoder = CpioDecoder::new(buffer);
    let entry_result = decoder.next().unwrap();
    assert!(entry_result.is_err());
    assert!(entry_result.err() == Some(Error::MagicError));
}
