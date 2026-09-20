# SPDX-License-Identifier: MPL-2.0
{
  mkShell,
  asterinas-rust-toolchain,
  asterinas-vdso,
  # Tools osdk/tools/docker/Dockerfile installs with `cargo install`; nixpkgs
  # provides them here, so versions may lag the Docker pins.
  cargo-binutils,
  cargo-expand,
  cargo-udeps,
  lychee,
  mdbook,
  mdbook-mermaid,
  typos,
  clang,
  clang-tools,
  git,
  python3,
  yq,
  jq,
  gnumake,
  pkg-config,
  file,
  nixfmt,
  asterinas-qemu,
  asterinas-grub,
  asterinas-ovmf,
  gdb,
  mtools,
  xorriso,
  cpio,
  dosfstools,
  exfatprogs,
  e2fsprogs,
  util-linux,
  parted,
  socat,
  strace,
  virtiofsd,
  iptables,
  iproute2,
  nix,
  wget,
  cachix,
}:

let
  cargoTools = [
    cargo-binutils
    cargo-expand
    cargo-udeps
    lychee
    mdbook
    mdbook-mermaid
    typos
  ];
  hostCommon = [
    clang
    clang-tools
    git
    python3
    yq
    jq
    gnumake
    pkg-config
    file
    # Match the formatter the prebuilt-nix-packages image installs.
    nixfmt
  ];
  bootAndHostTools = [
    asterinas-qemu
    asterinas-grub
    asterinas-ovmf
    # Disk-image and filesystem tools the kernel-dev image takes from Ubuntu.
    gdb
    mtools
    xorriso
    cpio
    dosfstools
    exfatprogs
    e2fsprogs
    util-linux
    parted
    socat
    strace
    virtiofsd
    iptables
    iproute2
    # tools/atomic_wget.sh downloads prebuilt artifacts for the benchmarks.
    wget
    # test/initramfs still builds images through nix-build, and
    # `make push_cachix` publishes the distro caches.
    nix
    cachix
  ];
in
mkShell {
  packages = [
    asterinas-rust-toolchain
  ]
  ++ cargoTools
  ++ hostCommon
  ++ bootAndHostTools;

  shellHook = ''
    # Change Cargo PATH order so Nix tools precede rustup shims.
    export PATH="$PATH:''${CARGO_HOME:-$HOME/.cargo}/bin"

    # Use the vDSO checkout pinned by the overlay unless the caller supplied one.
    export VDSO_LIBRARY_DIR="''${VDSO_LIBRARY_DIR:-${asterinas-vdso}}"

    # Use the Nix-built firmware unless the caller supplied another OVMF tree.
    export OVMF_DIR="''${OVMF_DIR:-${asterinas-ovmf}}"
  '';
}
