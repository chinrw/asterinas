// SPDX-License-Identifier: MPL-2.0

//! State transitions for file-descriptor slots reserved across fallible copy-out.
#![no_std]
#![deny(unsafe_code)]

/// A slot that is either hidden from lookup or contains a published file-table entry.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum FdSlot<T> {
    /// The descriptor number is allocated but must still behave as absent.
    Reserved,
    /// The descriptor is visible to lookup and close operations.
    Installed(T),
}

impl<T> FdSlot<T> {
    /// Allocates a descriptor number without publishing an entry.
    pub const fn reserved() -> Self {
        Self::Reserved
    }

    /// Returns the installed entry, or `None` while the descriptor is reserved.
    pub const fn installed(&self) -> Option<&T> {
        match self {
            Self::Reserved => None,
            Self::Installed(value) => Some(value),
        }
    }

    /// Returns the mutable installed entry, or `None` while the descriptor is reserved.
    pub const fn installed_mut(&mut self) -> Option<&mut T> {
        match self {
            Self::Reserved => None,
            Self::Installed(value) => Some(value),
        }
    }

    /// Returns whether the slot is still hidden from file-descriptor operations.
    pub const fn is_reserved(&self) -> bool {
        matches!(self, Self::Reserved)
    }

    /// Publishes an entry only if this slot is still reserved.
    pub fn install(&mut self, value: T) -> Result<(), T> {
        if !self.is_reserved() {
            return Err(value);
        }
        *self = Self::Installed(value);
        Ok(())
    }

    /// Extracts an installed entry without treating a reservation as a file.
    pub fn into_installed(self) -> Result<T, Self> {
        match self {
            Self::Installed(value) => Ok(value),
            Self::Reserved => Err(Self::Reserved),
        }
    }
}

#[cfg(test)]
mod tests {
    extern crate std;

    use loom::sync::{Arc, Mutex};

    use super::FdSlot;

    #[test]
    fn reserved_slot_is_not_visible() {
        let slot = FdSlot::<u8>::reserved();

        assert!(slot.installed().is_none());
    }

    #[test]
    fn reserved_slot_cannot_be_closed_or_reused_before_install() {
        loom::model(|| {
            let slot = Arc::new(Mutex::new(Some(FdSlot::<u8>::reserved())));
            let close_slot = slot.clone();
            let close = loom::thread::spawn(move || {
                let mut slot = close_slot.lock().unwrap();
                if slot.as_ref().and_then(FdSlot::installed).is_some() {
                    slot.take();
                }
            });
            let install_slot = slot.clone();
            let install = loom::thread::spawn(move || {
                let mut slot = install_slot.lock().unwrap();
                slot.as_mut().unwrap().install(7).unwrap();
            });

            close.join().unwrap();
            install.join().unwrap();
            let slot = slot.lock().unwrap();
            assert!(slot.is_none() || slot.as_ref().and_then(FdSlot::installed) == Some(&7));
        });
    }
}
