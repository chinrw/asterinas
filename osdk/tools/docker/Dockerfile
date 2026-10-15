# SPDX-License-Identifier: MPL-2.0

# This image supports development of OSDK-based kernels outside Asterinas.
# Include only tools needed to build OSDK, required by OSDK at runtime, or
# generally useful for developing an OSDK-based kernel.

FROM ubuntu:24.04 AS build-base

ARG DEBIAN_FRONTEND=noninteractive
ARG TARGETARCH

RUN apt update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    git \
    python3 \
    python3-venv \
    wget

#= The final stages to produce the OSDK development image ====================

FROM build-base AS osdk-final

ARG TARGETARCH

# For x86 kernel development on ARM, we need to add the sources. So that we can
# install x86 grub binaries from the Ubuntu repository.
RUN <<EOF
    if [ "${TARGETARCH}" = "arm64" ]; then
        dpkg --add-architecture 'amd64';
        sed -i '/^Components:/a Architectures: arm64' /etc/apt/sources.list.d/ubuntu.sources;
        cat >> /etc/apt/sources.list.d/ubuntu-amd64.sources <<EOF_SOURCES
Types: deb
URIs: http://archive.ubuntu.com/ubuntu/
Suites: noble noble-updates noble-backports
Components: main restricted universe multiverse
Architectures: amd64
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: http://security.ubuntu.com/ubuntu/
Suites: noble-security
Components: main restricted universe multiverse
Architectures: amd64
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF_SOURCES
    fi
EOF

# Install all OSDK dependent packages
RUN apt update \
    && apt install -y --no-install-recommends \
    clang               `# Used by the build script of minicov` \
    curl \
    gdb                 `# A part of OSDK debug support` \
    grub-efi-amd64      `# used for linux-efi-handover64 ISOs` \
    grub2-common        `# used for linux-efi-handover64 ISOs` \
    libsqlite3-dev      `# used by klint` \
    libssl-dev \
    mtools              `# used by grub-mkrescue` \
    pkg-config \
    xorriso \
    && apt clean \
    && rm -rf /var/lib/apt/lists/*

# Install Nix here so downstream images can use the same store and package manager.
# Installing Nix as root is not supported in single-user mode, so use `--daemon`.
# The prebuilt image registers channels pinned by `flake.lock`, so skip the
# installer's default `nixpkgs-unstable` channel.
RUN bash -c 'sh <(curl -L https://nixos.org/nix/install) --daemon --yes --no-channel-add'
ENV PATH="/nix/var/nix/profiles/default/bin:${PATH}"

# Install Rust with both nightly and stable
ENV PATH="/root/.cargo/bin:${PATH}"
ARG ASTER_RUST_VERSION
RUN curl https://sh.rustup.rs -sSf | \
        sh -s -- --default-toolchain ${ASTER_RUST_VERSION} -y \
    && rm -rf /root/.cargo/registry && rm -rf /root/.cargo/git \
    && cargo -V \
    && rustup component add rust-src rustc-dev llvm-tools-preview

# Let Github runners reuse the Rust toolchain installed in the Dockerfile and
# also the Cargo crates downloaded subsequently in the Docker build process.
RUN mkdir -p /github/home \
    && ln -s /root/.cargo /github/home/.cargo \
    && ln -s /root/.rustup /github/home/.rustup

# Install cargo tools
RUN cargo install --locked \
    cargo-expand@1.0.122 \
    lychee@0.24.2

# Install klint. Pin the Klint commit compatible with the Asterinas toolchain.
RUN cargo install klint --git https://github.com/Rust-for-Linux/klint --rev d2a4d60

COPY flake.nix flake.lock /tmp/asterinas/
COPY tools/dev_env/nix /tmp/asterinas/tools/dev_env/nix
# The prebuilt image clears automatic roots. Keep QEMU, GRUB, and the shared
# development tools under named roots so downstream garbage collection
# retains them and their runtime dependencies. OSDK opens `OVMF_VARS.fd` as
# writable pflash, so copy the firmware to its existing path instead of
# linking to the read-only store.
# Collect garbage in the same layer, so the nixpkgs source and any build
# inputs do not stay in the image.
RUN set -e; \
    for package in qemu grub dev-tools; do \
        nix --extra-experimental-features 'nix-command flakes' \
            build --accept-flake-config --no-update-lock-file \
            --out-link /nix/var/nix/gcroots/$package path:/tmp/asterinas#$package; \
    done; \
    nix --extra-experimental-features 'nix-command flakes' \
        build --accept-flake-config --no-update-lock-file \
        --out-link /tmp/ovmf path:/tmp/asterinas#ovmf; \
    mkdir -p /root/ovmf/release; \
    cp -R --no-preserve=mode /tmp/ovmf/. /root/ovmf/release; \
    ln -s /nix/var/nix/gcroots/qemu /usr/local/qemu; \
    ln -s /nix/var/nix/gcroots/grub /usr/local/grub; \
    rm -rf /tmp/asterinas /tmp/ovmf; \
    nix-store --gc
ENV PATH="/nix/var/nix/gcroots/dev-tools/bin:/usr/local/grub/bin:/usr/local/qemu/bin:${PATH}"

# Download and install Firecracker VMM
RUN ARCH=$(case ${TARGETARCH} in \
        amd64) echo "x86_64" ;; \
        arm64) echo "aarch64" ;; \
        *) ;; \
    esac) && \
    if [ -n "${ARCH}" ]; then \
        curl -L "https://github.com/firecracker-microvm/firecracker/releases/download/v1.16.1/firecracker-v1.16.1-${ARCH}.tgz" \
            | tar --no-same-owner -xz && \
        install -D -m 755 "release-v1.16.1-${ARCH}/firecracker-v1.16.1-${ARCH}" /usr/local/bin/firecracker && \
        rm -rf "release-v1.16.1-${ARCH}"; \
    fi

VOLUME [ "/root/asterinas" ]

WORKDIR /root/asterinas
