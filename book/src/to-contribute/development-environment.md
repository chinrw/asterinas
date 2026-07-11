# Development Environment

Building and testing Asterinas needs a pinned Rust toolchain
and a matching boot stack (QEMU, GRUB, OVMF).
Two environments provide this out of the box.

## Docker (canonical)

The
[Docker-based environment](https://github.com/asterinas/asterinas/blob/main/tools/docker/README.md)
is the canonical way to develop Asterinas:
it is what CI runs,
and the [kernel Getting Started guide](../kernel/index.md) uses it directly.
Use it unless you have a specific reason not to.

## Nix flake (alternative)

The
[Nix flake at the repository root](https://github.com/asterinas/asterinas/blob/main/nix/README.md)
offers an equivalent dev shell for Nix users,
with its toolchain and tool versions pinned to match the Docker image.
See its README for supported platforms, caveats,
and how to enter the shell automatically with direnv.
