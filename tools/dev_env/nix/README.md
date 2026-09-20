# Maintaining the Nix Development Environment

For setup and everyday commands, see [Using Nix for Development](../../../book/src/kernel/nix-development.md).
This directory contains the package definitions and shell configuration maintained alongside the Docker development environments.

## File organization

- The root [`flake.nix`](../../../flake.nix) declares inputs and exports Linux development shells and boot-stack packages.
- [`overlay.nix`](overlay.nix) assembles the Rust toolchain, vDSO source, and project-specific packages.
- [`devshell.nix`](devshell.nix) selects host tools and sets the shell environment.
- [`packages/`](packages/) contains the QEMU, GRUB, and firmware definitions.

The flake exports `x86_64-linux` and `aarch64-linux` shells.
The vendored edk2 expression retains upstream platform conditionals;
those conditionals do not add a supported Darwin shell.

## Maintaining dependency versions

The Rust toolchain is read from [`rust-toolchain.toml`](../../../rust-toolchain.toml).
The shell adds `rust-analyzer` from that same nightly.
Do not maintain a second Rust version in the Nix expressions.

The QEMU, GRUB, and edk2 versions follow the [OSDK Dockerfile](../../../osdk/tools/docker/Dockerfile).
The vDSO revision follows the [kernel development Dockerfile](../docker/kernel-dev/Dockerfile).
Each package records its source revision or version and content hash.
When updating a Docker dependency, update its Nix counterpart and verify the resulting package as well.

The main nixpkgs revision is shared with [`distro/nixpkgs.nix`](../../../distro/nixpkgs.nix)
and the [prebuilt Nix packages Dockerfile](../docker/prebuilt-nix-packages/Dockerfile).
The flake workflow checks these pins for consistency.
A separate nixpkgs input supplies the Docker-pinned `typos` version.
Other Cargo-installed tools are taken from the main nixpkgs input and may differ from the Docker versions.
The shell omits klint because no current build or check target invokes it.

When changing flake inputs, update and commit `flake.lock` with the corresponding expressions.
New local source files must be added to Git before a Git-backed `nix develop` can see them.
See the [Nix flake reference](https://nix.dev/manual/nix/stable/command-ref/new-cli/nix3-flake.html#types) for how local Git inputs are selected.

## Shell environment

The shell sets `VDSO_LIBRARY_DIR` and `OVMF_DIR` to Nix store paths unless the caller has already supplied values.
It appends the Cargo binary directory to `PATH` so that Nix-provided tools precede rustup proxies,
while locally installed commands such as `cargo-osdk` remain available.

The root `.envrc` watches `rust-toolchain.toml` and this directory in addition to the flake inputs.
Keep those watches aligned with the imported shell sources so that direnv reloads the environment after an edit.

Test packages are defined under [`test/initramfs/nix`](../../../test/initramfs/nix).
The shell provides Nix for the existing Make targets rather than duplicating those packages here.
Binary-cache publication must include the test outputs that should be reusable;
publishing only the development shell does not prebuild every test suite.

## Validation

From the repository root, check all exported systems without changing the lock file:

```bash
nix flake check --no-build --all-systems --no-update-lock-file
```

Build the boot-stack packages when changing their definitions:

```bash
nix build .#qemu .#grub .#ovmf
```

Check the shell and the x86-64 boot workflow with a reduced host environment:

```bash
nix develop --ignore-environment --keep HOME --command make check
nix develop --ignore-environment --keep HOME --command make run_kernel AUTO_TEST=boot
```

Evaluation checks the aarch64 output but does not establish that it builds or boots on an ARM64 host.
Record the architecture and commands used when reporting runtime validation.

Use `make format` and `make check` for repository formatting.
For a focused Nix check, pass explicit paths to the shared formatter:

```bash
./tools/nixfmt.sh --check -- flake.nix tools/dev_env/nix
```
