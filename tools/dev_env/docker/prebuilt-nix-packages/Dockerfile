# SPDX-License-Identifier: MPL-2.0

# This image adds Nix tooling and cached Asterinas test packages to the OSDK image.

ARG BASE_VERSION
FROM asterinas/osdk-dev:${BASE_VERSION} AS build-base

SHELL ["/bin/bash", "-c"]

ARG DEBIAN_FRONTEND=noninteractive

# Install Nix package manager
#
# FIXME: Installing Nix as root is not supported in single-user mode.
RUN sh <(curl -L https://nixos.org/nix/install) --daemon --yes

# Pin the nixpkgs and nixos channels to the revision in flake.lock and install
# the other Nix tools from them. The lock stays in the image, so a published
# image can be traced back to the revisions it was built from.
COPY flake.lock /usr/local/share/asterinas/flake.lock
RUN . /etc/profile.d/nix.sh \
    && rev="$(nix-instantiate --eval --raw --expr \
        '(builtins.fromJSON (builtins.readFile /usr/local/share/asterinas/flake.lock)).nodes.nixpkgs.locked.rev')" \
    && test -n "$rev" \
    && nix-channel --add "https://github.com/NixOS/nixpkgs/archive/${rev}.tar.gz" nixpkgs \
    && nix-channel --add "https://github.com/NixOS/nixpkgs/archive/${rev}.tar.gz" nixos \
    && nix-channel --update \
    && nix-env -iA nixpkgs.nixfmt nixpkgs.nixos-install-tools nixpkgs.cachix \
    && rm /nix/var/nix/gcroots/auto/* \
    && nix-collect-garbage -d

#= Build Nix packages ======================================================

COPY test/initramfs /tmp/asterinas/test/initramfs
COPY flake.nix flake.lock /tmp/asterinas/
COPY tools/nix /tmp/asterinas/tools/nix
WORKDIR /tmp/asterinas/test/initramfs
ENV PATH="/nix/var/nix/profiles/default/bin:${PATH}"

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

# Let Github runners reuse Nixpkgs tarball installed in the Dockerfile.
RUN mkdir -p /github/home/.cache \
    && ln -s /root/.cache/nix /github/home/.cache/nix

VOLUME [ "/root/asterinas" ]

WORKDIR /root/asterinas
