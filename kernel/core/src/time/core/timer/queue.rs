// SPDX-License-Identifier: MPL-2.0

//! Timer queue types backed by the owner-checked OSTD collection.

use super::TimerCallbackData;

pub(super) type TimerQueue = timer_queue::TimerQueue<TimerCallbackData>;
pub(super) type TimerFactory = timer_queue::Factory<TimerCallbackData>;
