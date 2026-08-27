# SPDX-License-Identifier: MPL-2.0
#
# Verifies the device-number codec inside the `.#kani` dev shell:
# the boundary unit tests, then every Kani harness listed below.
#
# The explicit harness list keeps the proofs themselves honest: deleting
# or renaming a harness must turn this script red instead of silently
# shrinking the verified surface. Update the list when adding a proof.
set -euo pipefail

cd "$(dirname "$0")/../.."

file=kernel/libs/device-id/src/encoding.rs
harnesses=(
  proofs::documented_limits_are_enforced
  proofs::encoded_fields_match_linux_layout
  proofs::encoded_pairs_are_classified_exactly
  proofs::pair_round_trip
  proofs::raw_round_trip
  proofs::validated_decode_acceptance_is_exact
  proofs::validated_decode_preserves_decoded_values
)

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

# The codec file is dependency-free, so its #[cfg(test)] tests can run on
# the host even though the crate itself only builds as part of the kernel.
rustc --edition 2024 --test "$file" -o "$workdir/boundary-tests"
"$workdir/boundary-tests"

listing=$(kani list "$file")
for harness in "${harnesses[@]}"; do
  if ! grep -qF "$harness" <<<"$listing"; then
    echo "error: expected harness '$harness' not found in $file" >&2
    exit 1
  fi
done

kani "$file" --output-format terse | tee "$workdir/kani.log"

# An exact count also catches a harness added without updating the list.
summary="Complete - ${#harnesses[@]} successfully verified harnesses, 0 failures, ${#harnesses[@]} total."
if ! grep -qF "$summary" "$workdir/kani.log"; then
  echo "error: expected verification summary '$summary'" >&2
  exit 1
fi
