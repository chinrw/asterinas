#!/bin/bash

# SPDX-License-Identifier: MPL-2.0

# Fails when a version/rev/commit that is duplicated between the Docker-based
# dev image and the Nix flake packaging drifts out of sync. Whoever bumps one
# side is expected to bump the other in the same change; this script is the
# safety net for when that is forgotten.
#
# Each pin below is extracted with `sed`, anchored on the surrounding literal
# text rather than a line number, so reordering the files does not break the
# check (only renaming the anchor text does, in which case update the pattern
# here alongside it).

set -euo pipefail

PROJECT_ROOT="$(realpath "$(dirname "${BASH_SOURCE[0]}")/..")"
cd "$PROJECT_ROOT"

# extract FILE SED_PATTERN
# Prints the first capture-group match, or nothing if the anchor text is not
# found (e.g. because the file was reworded and the pattern needs updating).
extract() {
    local file="$1"
    local pattern="$2"

    sed -nE "$pattern" "$file" 2>/dev/null | head -n1 || true
}

fail=0
rows=()

# add_pin NAME FILE_A VALUE_A FILE_B VALUE_B MODE
# MODE is "exact" (values must match verbatim) or "prefix" (VALUE_A, the
# Docker image's short rev, must be a prefix of VALUE_B, the Nix pin's full
# rev).
add_pin() {
    local name="$1" file_a="$2" value_a="$3" file_b="$4" value_b="$5" mode="$6"
    local status="OK"

    if [[ -z "$value_a" || -z "$value_b" ]]; then
        status="ERROR (pin not found; update check_version_pins.sh)"
        fail=1
    elif [[ "$mode" == "prefix" ]]; then
        case "$value_b" in
            "$value_a"*) ;;
            *)
                status="MISMATCH"
                fail=1
                ;;
        esac
    else
        if [[ "$value_a" != "$value_b" ]]; then
            status="MISMATCH"
            fail=1
        fi
    fi

    rows+=("$name|$file_a|$value_a|$file_b|$value_b|$status")
}

#= QEMU version ==============================================================

qemu_docker="$(extract osdk/tools/docker/Dockerfile \
    's/.*qemu-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.xz.*/\1/p')"
qemu_nix="$(extract nix/packages/qemu.nix \
    's/.*version = "([0-9]+\.[0-9]+\.[0-9]+)";.*/\1/p')"
add_pin "QEMU version" \
    "osdk/tools/docker/Dockerfile" "$qemu_docker" \
    "nix/packages/qemu.nix" "$qemu_nix" \
    exact

#= edk2 tag ===================================================================

edk2_docker="$(extract osdk/tools/docker/Dockerfile \
    's/.*--branch edk2-stable([0-9]+) .*/\1/p')"
edk2_nix="$(extract nix/packages/edk2.nix \
    's/.*version = "([0-9]{6})";.*/\1/p')"
add_pin "edk2 tag (edk2-stable<date>)" \
    "osdk/tools/docker/Dockerfile" "$edk2_docker" \
    "nix/packages/edk2.nix" "$edk2_nix" \
    exact

#= GRUB fork rev ==============================================================

grub_docker="$(extract osdk/tools/docker/Dockerfile \
    's/.*git -C grub checkout ([0-9a-f]+).*/\1/p')"
grub_nix="$(extract nix/packages/grub.nix \
    's/.*rev = "([0-9a-f]{40})";.*/\1/p')"
add_pin "GRUB fork rev" \
    "osdk/tools/docker/Dockerfile" "$grub_docker" \
    "nix/packages/grub.nix" "$grub_nix" \
    prefix

#= klint rev ===================================================================

klint_docker="$(extract osdk/tools/docker/Dockerfile \
    's/.*klint --rev ([0-9a-f]+).*/\1/p')"
klint_nix="$(extract nix/packages/klint.nix \
    's/.*rev = "([0-9a-f]{40})";.*/\1/p')"
add_pin "klint rev" \
    "osdk/tools/docker/Dockerfile" "$klint_docker" \
    "nix/packages/klint.nix" "$klint_nix" \
    prefix

#= linux_vdso rev =============================================================

vdso_docker="$(extract tools/docker/Dockerfile \
    's/.*git checkout ([0-9a-f]+).*/\1/p')"
vdso_nix="$(extract nix/overlay.nix \
    's/.*rev = "([0-9a-f]{40})";.*/\1/p')"
add_pin "linux_vdso rev" \
    "tools/docker/Dockerfile" "$vdso_docker" \
    "nix/overlay.nix" "$vdso_nix" \
    prefix

#= nixpkgs commit ==============================================================

nixpkgs_docker="$(extract tools/docker/nix/Dockerfile \
    's#.*NixOS/nixpkgs/archive/([0-9a-f]{40})\.tar\.gz.*#\1#p')"
nixpkgs_flake="$(extract flake.nix \
    's#.*NixOS/nixpkgs/([0-9a-f]{40})".*#\1#p')"
add_pin "nixpkgs commit" \
    "tools/docker/nix/Dockerfile" "$nixpkgs_docker" \
    "flake.nix" "$nixpkgs_flake" \
    exact
# The initramfs package set pins the same nixpkgs snapshot a third time.
nixpkgs_initramfs="$(extract test/initramfs/nix/default.nix \
    's#.*NixOS/nixpkgs/archive/([0-9a-f]{40})\.tar\.gz.*#\1#p')"
add_pin "nixpkgs commit (initramfs)" \
    "test/initramfs/nix/default.nix" "$nixpkgs_initramfs" \
    "flake.nix" "$nixpkgs_flake" \
    exact

#= Report ======================================================================

if ((fail)); then
    echo "Version pins are out of sync between the Docker image and the Nix flake:" >&2
    echo >&2
    printf '%-28s  %-52s  %-52s  %s\n' "PIN" "SIDE A" "SIDE B" "STATUS" >&2
    for row in "${rows[@]}"; do
        IFS='|' read -r name file_a value_a file_b value_b status <<< "$row"
        printf '%-28s  %-52s  %-52s  %s\n' \
            "$name" "${file_a}: ${value_a:-<missing>}" "${file_b}: ${value_b:-<missing>}" "$status" >&2
    done
    exit 1
fi

echo "Version pins OK: Docker image and Nix flake agree on ${#rows[@]} duplicated pins."
