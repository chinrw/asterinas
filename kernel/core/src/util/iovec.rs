// SPDX-License-Identifier: MPL-2.0

use aster_uapi::{
    IoVec, IovecError, MAX_IO_VECTOR_LENGTH, MAX_TOTAL_IOV_BYTES, UserIoVec, iovec_entry_addr,
};
use ostd::{
    Error as OstdError,
    mm::{Infallible, VmSpace},
};

use crate::prelude::*;

fn iovec_reader<'a>(iovec: &IoVec, vm_space: &'a VmSpace) -> Result<VmReader<'a>> {
    Ok(vm_space.reader(iovec.base(), iovec.len())?)
}

fn iovec_writer<'a>(iovec: &IoVec, vm_space: &'a VmSpace) -> Result<VmWriter<'a>> {
    Ok(vm_space.writer(iovec.base(), iovec.len())?)
}

/// The util function for create [`VmReader`]/[`VmWriter`]s.
fn copy_iovs_and_convert<'a, T: 'a>(
    user_space: &'a CurrentUserSpace<'a>,
    start_addr: Vaddr,
    count: usize,
    convert_iovec: impl Fn(&IoVec, &'a VmSpace) -> Result<T>,
) -> Result<Box<[T]>> {
    if count > MAX_IO_VECTOR_LENGTH {
        return_errno_with_message!(Errno::EINVAL, "the I/O vector contains too many buffers");
    }

    let vm_space = user_space.vmar().vm_space();

    let mut v = Vec::with_capacity(count);
    let mut max_len = MAX_TOTAL_IOV_BYTES;

    for idx in 0..count {
        let mut iov = {
            let Some(addr) = iovec_entry_addr(start_addr, idx) else {
                return_errno_with_message!(Errno::EFAULT, "the I/O vector address overflows");
            };
            let uiov: UserIoVec = vm_space.reader(addr, size_of::<UserIoVec>())?.read_val()?;
            match uiov.validate() {
                Ok(iovec) => iovec,
                Err(IovecError::NegativeLength) => {
                    return_errno_with_message!(
                        Errno::EINVAL,
                        "the I/O buffer length cannot be negative"
                    );
                }
            }
        };

        // Truncate the buffer if the number of bytes exceeds `MAX_TOTAL_IOV_BYTES`.
        // See comments above the `MAX_TOTAL_IOV_BYTES` constant for more details.
        max_len = iov.truncate_to(max_len);

        if iov.is_empty() {
            continue;
        }

        let converted = convert_iovec(&iov, vm_space)?;
        v.push(converted)
    }

    Ok(v.into_boxed_slice())
}

/// A collection of [`VmReader`]s.
///
/// Such readers are built from user-provided buffer, so it's always fallible.
pub(crate) struct VmReaderArray<'a>(Box<[VmReader<'a>]>);

/// A collection of [`VmWriter`]s.
///
/// Such writers are built from user-provided buffer, so it's always fallible.
pub(crate) struct VmWriterArray<'a>(Box<[VmWriter<'a>]>);

impl<'a> VmReaderArray<'a> {
    /// Creates a new `VmReaderArray` from user-provided I/O vector buffers.
    ///
    /// This ensures that empty buffers are filtered out, meaning that all of the returned readers
    /// should be non-empty.
    pub(crate) fn from_user_io_vecs(
        user_space: &'a CurrentUserSpace<'a>,
        start_addr: Vaddr,
        count: usize,
    ) -> Result<Self> {
        let readers = copy_iovs_and_convert(user_space, start_addr, count, iovec_reader)?;
        Ok(Self(readers))
    }

    /// Returns mutable reference to [`VmReader`]s.
    pub(crate) fn readers_mut(&mut self) -> &mut [VmReader<'a>] {
        &mut self.0
    }

    /// Creates a new `VmReaderArray`.
    #[cfg(ktest)]
    pub(crate) const fn new(readers: Box<[VmReader<'a>]>) -> Self {
        Self(readers)
    }
}

impl<'a> VmWriterArray<'a> {
    /// Creates a new `VmWriterArray` from user-provided I/O vector buffers.
    ///
    /// This ensures that empty buffers are filtered out, meaning that all of the returned writers
    /// should be non-empty.
    pub(crate) fn from_user_io_vecs(
        user_space: &'a CurrentUserSpace<'a>,
        start_addr: Vaddr,
        count: usize,
    ) -> Result<Self> {
        let writers = copy_iovs_and_convert(user_space, start_addr, count, iovec_writer)?;
        Ok(Self(writers))
    }

    /// Returns mutable reference to [`VmWriter`]s.
    pub(crate) fn writers_mut(&mut self) -> &mut [VmWriter<'a>] {
        &mut self.0
    }
}

/// Trait defining the read behavior for a collection of [`VmReader`]s.
pub(crate) trait MultiRead: ReadCString {
    /// Reads the exact number of bytes required to exhaust `self` or fill `writer`,
    /// accumulating total bytes read.
    ///
    /// If the return value is `Ok(n)`,
    /// then `n` should be `min(self.sum_lens(), writer.avail())`.
    ///
    /// # Errors
    ///
    /// This method returns [`OstdError::PageFault`] if a page fault occurs, along with
    /// the number of bytes copied before the error occurs. When an error is returned,
    /// both `self` and `writer` are advanced by the returned byte count.
    fn read(&mut self, writer: &mut VmWriter<'_, Infallible>) -> Result<usize, (OstdError, usize)>;

    /// Calculates the total length of data remaining to read.
    fn sum_lens(&self) -> usize;

    /// Checks if the data remaining to read is empty.
    fn is_empty(&self) -> bool {
        self.sum_lens() == 0
    }

    /// Skips the first `nbytes` bytes of data, or skips to the end if the readers have
    /// insufficient bytes.
    fn skip_some(&mut self, nbytes: usize);
}

/// Trait defining the write behavior for a collection of [`VmWriter`]s.
pub(crate) trait MultiWrite {
    /// Writes the exact number of bytes required to exhaust `writer` or fill `self`,
    /// accumulating total bytes read.
    ///
    /// If the return value is `Ok(n)`,
    /// then `n` should be `min(self.sum_lens(), reader.remain())`.
    ///
    /// # Errors
    ///
    /// This method returns [`OstdError::PageFault`] if a page fault occurs, along with
    /// the number of bytes copied before the error occurs. When an error is returned,
    /// both `self` and `reader` are advanced by the returned byte count.
    fn write(&mut self, reader: &mut VmReader<'_, Infallible>)
    -> Result<usize, (OstdError, usize)>;

    /// Calculates the length of space available to write.
    fn sum_lens(&self) -> usize;

    /// Checks if the space available to write is empty.
    fn is_empty(&self) -> bool {
        self.sum_lens() == 0
    }

    /// Skips the first `nbytes` bytes of data, or skips to the end if the writers have
    /// insufficient bytes.
    fn skip_some(&mut self, nbytes: usize);
}

impl MultiRead for VmReaderArray<'_> {
    fn read(&mut self, writer: &mut VmWriter<'_, Infallible>) -> Result<usize, (OstdError, usize)> {
        let mut total_len = 0;

        for reader in &mut self.0 {
            let copied_len = reader
                .read_fallible(writer)
                .map_err(|(err, copied_len)| (err, total_len + copied_len))?;
            total_len += copied_len;
            if !writer.has_avail() {
                break;
            }
        }
        Ok(total_len)
    }

    fn sum_lens(&self) -> usize {
        self.0.iter().map(|vm_reader| vm_reader.remain()).sum()
    }

    fn skip_some(&mut self, mut nbytes: usize) {
        for reader in &mut self.0 {
            let bytes_to_skip = reader.remain().min(nbytes);
            reader.skip(bytes_to_skip);
            nbytes -= bytes_to_skip;

            if nbytes == 0 {
                return;
            }
        }
    }
}

impl MultiRead for VmReader<'_> {
    fn read(&mut self, writer: &mut VmWriter<'_, Infallible>) -> Result<usize, (OstdError, usize)> {
        self.read_fallible(writer)
    }

    fn sum_lens(&self) -> usize {
        self.remain()
    }

    fn skip_some(&mut self, nbytes: usize) {
        self.skip(self.remain().min(nbytes));
    }
}

impl dyn MultiRead + '_ {
    /// Reads a `T` value, returning a `None` if the readers have insufficient bytes.
    pub(crate) fn read_val_opt<T: Pod>(&mut self) -> Result<Option<T>> {
        let mut val = T::new_zeroed();
        let nbytes = self.read(&mut VmWriter::from(val.as_mut_bytes()))?;

        if nbytes == size_of::<T>() {
            Ok(Some(val))
        } else {
            Ok(None)
        }
    }
}

impl MultiWrite for VmWriterArray<'_> {
    fn write(
        &mut self,
        reader: &mut VmReader<'_, Infallible>,
    ) -> Result<usize, (OstdError, usize)> {
        let mut total_len = 0;

        for writer in &mut self.0 {
            let copied_len = writer
                .write_fallible(reader)
                .map_err(|(err, copied_len)| (err, total_len + copied_len))?;
            total_len += copied_len;
            if !reader.has_remain() {
                break;
            }
        }
        Ok(total_len)
    }

    fn sum_lens(&self) -> usize {
        self.0.iter().map(|vm_writer| vm_writer.avail()).sum()
    }

    fn skip_some(&mut self, mut nbytes: usize) {
        for writer in &mut self.0 {
            let bytes_to_skip = writer.avail().min(nbytes);
            writer.skip(bytes_to_skip);
            nbytes -= bytes_to_skip;

            if nbytes == 0 {
                return;
            }
        }
    }
}

impl MultiWrite for VmWriter<'_> {
    fn write(
        &mut self,
        reader: &mut VmReader<'_, Infallible>,
    ) -> Result<usize, (OstdError, usize)> {
        self.write_fallible(reader)
    }

    fn sum_lens(&self) -> usize {
        self.avail()
    }

    fn skip_some(&mut self, nbytes: usize) {
        self.skip(self.avail().min(nbytes));
    }
}

impl dyn MultiWrite + '_ {
    /// Writes a `T` value, truncating the value if the writers have insufficient bytes.
    pub(crate) fn write_val_trunc<T: Pod>(&mut self, val: &T) -> Result<()> {
        let _nbytes = self.write(&mut VmReader::from(val.as_bytes()))?;
        // `_nbytes` may be smaller than the value size. We ignore it to truncate the value.

        Ok(())
    }
}
