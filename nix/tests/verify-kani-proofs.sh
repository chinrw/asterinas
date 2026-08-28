# SPDX-License-Identifier: MPL-2.0
#
# Runs every in-tree Kani proof inside the `.#kani` dev shell, plus the
# host-runnable unit tests of the files that carry them.
#
# Each entry below names a file and the exact harnesses it must contain.
# The explicit list keeps the proofs honest: deleting, renaming, or
# adding a harness has to turn this script red rather than silently
# change the verified surface. Update the list when adding a proof.
set -euo pipefail

cd "$(dirname "$0")/../.."

# file:harness,harness,...
targets=(
  "kernel/libs/device-id/src/encoding.rs:\
documented_limits_are_enforced,\
encoded_fields_match_linux_layout,\
encoded_pairs_are_classified_exactly,\
pair_round_trip,\
raw_round_trip,\
validated_decode_acceptance_is_exact,\
validated_decode_preserves_decoded_values"

  "kernel/libs/aster-util/src/coeff.rs:\
multiplying_within_a_clock_source_bound_never_overflows,\
new_is_panic_free_for_the_clocksource_shape"

  "kernel/libs/aster-util/src/fixed_point.rs:\
dividing_by_one_is_the_identity,\
dividing_by_zero_is_rejected,\
multiplying_by_one_is_the_identity,\
multiplying_by_zero_is_zero,\
saturating_from_num_keeps_representable_integers,\
widening_a_load_average_preserves_its_value"
)

# These files are modules of in-kernel crates, so `make test` never
# reaches them; Kani compiles them standalone, and so can rustc.
host_tested=(kernel/libs/device-id/src/encoding.rs)

# Kani compiles a standalone file at the 2015 edition by default, which
# rejects the `use core::...` paths these modules rely on.
export RUSTFLAGS="${RUSTFLAGS:+$RUSTFLAGS }--edition 2024"

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

for file in "${host_tested[@]}"; do
  rustc --edition 2024 --test "$file" -o "$workdir/host-tests"
  "$workdir/host-tests"
done

for target in "${targets[@]}"; do
  file=${target%%:*}
  IFS=, read -ra harnesses <<<"${target#*:}"

  listing=$(kani list "$file")
  for harness in "${harnesses[@]}"; do
    # Anchor the end of the name: a plain substring match also accepts a
    # harness renamed to <name>_disabled, which would drop it from the
    # verified surface while the count below still adds up.
    if ! grep -qE "proofs::${harness}([^A-Za-z0-9_]|\$)" <<<"$listing"; then
      echo "error: expected harness 'proofs::$harness' not found in $file" >&2
      exit 1
    fi
  done

  log=$workdir/$(basename "$file").log
  kani "$file" --output-format terse | tee "$log"

  # An exact count also catches a harness added without updating the list.
  count=${#harnesses[@]}
  summary="Complete - $count successfully verified harnesses, 0 failures, $count total."
  if ! grep -qF "$summary" "$log"; then
    echo "error: $file: expected verification summary '$summary'" >&2
    exit 1
  fi
done
