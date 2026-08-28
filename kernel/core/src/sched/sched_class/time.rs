// SPDX-License-Identifier: MPL-2.0

use spin::Once;

/// Returns the numerator and denominator of the ratio R:
///
///     R = 10^9 (ns in a sec) / TSC clock frequency
fn tsc_factors() -> (u64, u64) {
    static FACTORS: Once<(u64, u64)> = Once::new();
    *FACTORS.call_once(|| {
        let freq = ostd::arch::tsc_freq();
        assert_ne!(freq, 0);
        let gcd = gcd(1_000_000_000, freq);
        (1_000_000_000 / gcd, freq / gcd)
    })
}

/// Computes the greatest common divisor by the Euclidean algorithm.
///
/// `gcd(a, 0)` and `gcd(0, b)` return the other operand, so the result is
/// non-zero whenever at least one operand is.
fn gcd(mut a: u64, mut b: u64) -> u64 {
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

/// The base time slice allocated for every thread, measured in nanoseconds.
pub(crate) const BASE_SLICE_NS: u64 = 750_000;

/// The minimum scheduling period, measured in nanoseconds.
pub(crate) const MIN_PERIOD_NS: u64 = 6_000_000;

fn consts() -> (u64, u64) {
    static CONSTS: Once<(u64, u64)> = Once::new();
    *CONSTS.call_once(|| {
        let (a, b) = tsc_factors();
        (BASE_SLICE_NS * b / a, MIN_PERIOD_NS * b / a)
    })
}

/// Returns the base time slice allocated for every thread, measured in TSC clock units.
pub(crate) fn base_slice_clocks() -> u64 {
    consts().0
}

/// Returns the minimum scheduling period, measured in TSC clock units.
pub(crate) fn min_period_clocks() -> u64 {
    consts().1
}

#[cfg(ktest)]
mod tests {
    use ostd::prelude::ktest;

    use super::gcd;

    #[ktest]
    fn gcd_of_coprime_values_is_one() {
        // The previous loop exited once an operand reached 1 and then
        // returned the other operand (e.g. gcd(7, 3) came out as 3).
        assert_eq!(gcd(7, 3), 1);
        assert_eq!(gcd(1_000_000_000, 333_333_331), 1);
    }

    #[ktest]
    fn gcd_of_typical_tsc_frequencies() {
        assert_eq!(gcd(1_000_000_000, 2_400_000_000), 200_000_000);
        assert_eq!(gcd(1_000_000_000, 1_000_000_000), 1_000_000_000);
        assert_eq!(gcd(1_000_000_000, 3), 1);
    }

    #[ktest]
    fn gcd_handles_zero_operands() {
        assert_eq!(gcd(5, 0), 5);
        assert_eq!(gcd(0, 5), 5);
    }
}
