# SPDX-License-Identifier: MPL-2.0

# This Docker image adds Nix utilities and prebuilt test packages on top of
# the OSDK development Docker image.

ARG BASE_VERSION
FROM asterinas/osdk-dev:${BASE_VERSION} AS build-base

SHELL ["/bin/bash", "-c"]

ARG DEBIAN_FRONTEND=noninteractive

COPY flake.nix flake.lock /tmp/asterinas/
COPY distro/nixpkgs.nix /tmp/asterinas/distro/nixpkgs.nix

# Install Nix utilities and pin `nixpkgs` for interactive Nix commands.
#
# * `/nix/var/nix/gcroots/nixpkgs`: keeps the locked `nixpkgs` source tree
#   across garbage-collection runs during this Docker image build and in
#   downstream Docker images.
# * `nix-path`, `/etc/nix/registry.json`, and `/root/.nix-defexpr/nixpkgs`:
#   expose that store path to `<nixpkgs>`, bare `nixpkgs#` references, and
#   `nix-env -iA nixpkgs.<pkg>` without registering channels.
# * `/etc/nix/flake-registry.json`: caches the global flake registry locally so
#   flake commands work offline while preserving non-`nixpkgs` registry entries.
RUN <<'EOF'
set -euo pipefail
nixpkgs_path="$(nix-instantiate --eval --strict --json --expr \
    'toString (import /tmp/asterinas/distro/nixpkgs.nix {}).path' \
    | python3 -c 'import json, sys; print(json.load(sys.stdin))')"
ln -sfn "${nixpkgs_path}" /nix/var/nix/gcroots/nixpkgs
curl -fsSL "$(nix config show flake-registry)" -o /etc/nix/flake-registry.json
python3 - "${nixpkgs_path}" <<'PYTHON'
import json
import sys

with open("/etc/nix/flake-registry.json") as f:
    json.load(f)

registry = {
    "version": 2,
    "flakes": [
        {
            "exact": True,
            "from": {"type": "indirect", "id": "nixpkgs"},
            "to": {"type": "path", "path": sys.argv[1]},
        }
    ],
}
with open("/etc/nix/registry.json", "w") as f:
    json.dump(registry, f, indent=2)
    f.write("\n")
PYTHON
printf '%s\n' \
    'nix-path = nixpkgs=/nix/var/nix/gcroots/nixpkgs' \
    'flake-registry = /etc/nix/flake-registry.json' \
    >> /etc/nix/nix.conf
mkdir -m 0700 -p /root/.nix-defexpr
ln -sfn /nix/var/nix/gcroots/nixpkgs /root/.nix-defexpr/nixpkgs
nix-env -f /tmp/asterinas/distro/nixpkgs.nix \
    -iA nixfmt \
    -A nixos-install-tools \
    -A cachix
rm -f /nix/var/nix/gcroots/auto/*
nix-collect-garbage -d
EOF

#= Build Nix packages ======================================================

COPY test/initramfs /tmp/asterinas/test/initramfs
WORKDIR /tmp/asterinas/test/initramfs

# Build aarch64 packages
RUN make aarch64_pkgs VERBOSE=0 \
    && make nix_gc

# Build riscv64 packages
RUN make riscv64_pkgs VERBOSE=0 \
    && make nix_gc

# Build x86_64 packages
RUN make x86_64_pkgs VERBOSE=0 \
    && make nix_gc

# Install host required packages
RUN make install_host_pkgs \
    && make nix_gc

# Build initramfs packages to accelerate CI workflows
#
# It could also cache `stdenvNoCC`, a fundamental and predefined build environment.
# Therefore, `make nix_gc` MUST NOT be run after this.
RUN make initramfs_pkgs VERBOSE=0

# Clean source files
RUN rm -rf /tmp/asterinas

VOLUME [ "/root/asterinas" ]

WORKDIR /root/asterinas
