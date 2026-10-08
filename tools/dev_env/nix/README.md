# Maintaining the Nix Development Environment

This document is for developers who edit the Nix files in this directory.
If you only want to use the shell, read
[Using Nix for Development](../../../book/src/kernel/nix-development.md) instead.

## File organization

- The root [`flake.nix`](../../../flake.nix) declares the inputs
  and exports the development shells, boot-stack packages, and vDSO files
  for `x86_64-linux` and `aarch64-linux`.
- [`overlay.nix`](overlay.nix) assembles the Rust toolchain, the vDSO source,
  and the project-specific packages into a nixpkgs overlay.
- [`devshell.nix`](devshell.nix) selects the host tools
  and sets the environment variables of the shell.
- [`packages/`](packages/) contains the definitions of QEMU, GRUB, and the OVMF firmware.
- The test suites are packaged under [`test/initramfs/nix`](../../../test/initramfs/nix), not here.
  The shell only provides the `nix` command that the existing Make targets use to build them.

The definitions under `packages/` select the QEMU, GRUB, and OVMF versions used for development
and adjust nixpkgs' patches and build settings where needed.
Comments in each package definition explain its deviations from nixpkgs.

The shell sets `GRUB_MKRESCUE` to its packaged GRUB executable
so that the `iso` and `nixos` targets do not depend on `/usr/bin/grub-mkrescue`.

The host-side clients of the network benchmarks come from
[`test/initramfs/nix`](../../../test/initramfs/nix/default.nix),
the definitions that the Docker image installs with `make install_host_pkgs`.

## Dependency versions

The Rust toolchain is read from [`rust-toolchain.toml`](../../../rust-toolchain.toml),
and the shell adds `rust-analyzer` from the same nightly.
Never pin a second Rust version in the Nix expressions.

The GRUB version and the edk2 release used to build OVMF follow the
[OSDK Dockerfile](../../../osdk/tools/docker/Dockerfile).
When you bump one of these dependencies in a Dockerfile,
bump its Nix counterpart in the same change.
Nothing in CI compares the two yet, so a Dockerfile-only bump passes unnoticed.
Until such a check exists, verify the pins by hand.
If you do not have Nix installed,
update the version or revision, set the `hash` to an empty string,
and let the [Test Nix flake workflow](../../../.github/workflows/test_nix_flake.yml) run.
That first run is expected to fail with a hash mismatch.
The error prints the real hash after `got:`, so copy that value into the file.
The workflow then rebuilds the packages and boots the kernel from them.

The development shell and [OSDK image](../../../osdk/tools/docker/Dockerfile)
use the QEMU definition in [`packages/qemu.nix`](packages/qemu.nix).
To update QEMU, change its version and hash there, then run `nix build .#qemu`.
Rebuild the OSDK image and its downstream images to use the updated package.

The development shell and [kernel development image](../docker/kernel-dev/Dockerfile)
use the vDSO files defined by `asterinas-vdso` in [`overlay.nix`](overlay.nix).
To update them, change that definition's revision and hash, then run `nix build .#vdso`.
Rebuild the Docker image to include the updated files.

The development shell and Make-based builds take their main nixpkgs source from `flake.lock`.
To update it, run `nix flake update nixpkgs` from the repository root, either
on a host with Nix installed or inside the project development container.
Review the lock diff, then run the validation commands below.
If Nix reports that `nix-command` or `flakes` is disabled,
add `--extra-experimental-features 'nix-command flakes'` to the command.

The [prebuilt Nix packages image](../docker/prebuilt-nix-packages/Dockerfile)
uses the locked revision to create its channels during the image build.
Published images keep those channels when you update the checkout.
The "Check nixpkgs source" step in the
[Test Nix flake workflow](../../../.github/workflows/test_nix_flake.yml)
compares the Flake source with the source used by [`distro/nixpkgs.nix`](../../../distro/nixpkgs.nix).

Make-based builds still read the lock file when you use temporary Flake input overrides.
Update the lock to check a dependency change across all entry points.

The `typos` version is pinned to the one in the OSDK Dockerfile
through a separate nixpkgs input, because that Dockerfile checks the spelling with a fixed release.
The other tools that the Dockerfile installs with `cargo install`
come from the main nixpkgs input and may be older or newer than the Docker versions.
The shell omits klint, because no build or check target invokes it.

## Docker builds

The OSDK image installs Nix and builds `.#qemu`.
Downstream images inherit that Nix installation and store.
Both QEMU and vDSO builds accept the Flake's Cachix configuration,
so Nix can download matching cached outputs instead of rebuilding them.

QEMU has a named garbage-collection root under `/nix/var/nix/gcroots/qemu`.
Preserve it when changing the prebuilt image's cleanup steps,
which remove automatic roots before building the test packages.
Also preserve the final initramfs warm-up: its build dependencies are kept for later builds,
so the Dockerfile deliberately does not run garbage collection afterward.

Build the image chain with a new shared tag, starting with `osdk-dev`,
then `prebuilt-nix-packages`, `kernel-dev`, and `dev`.
The updated prebuilt Dockerfile requires Nix from the new OSDK image.
Publish the required platforms before changing Make or CI callers to use the new tag.
The publication workflow skips tags that already exist.

## Validation

From the repository root, evaluate every exported system without touching the lock file:

```bash
nix flake check --no-build --all-systems --no-update-lock-file
```

When you change a package definition, build the boot-stack packages:

```bash
nix build .#qemu .#grub .#ovmf
```

Then reproduce the CI checks, which run the shell with a stripped-down host environment:

```bash
nix develop --ignore-environment --keep HOME --command make check
nix develop --ignore-environment --keep HOME --command make run_kernel AUTO_TEST=boot
```

CI runs the two commands above on an ARM64 runner as well,
with `ENABLE_KVM=0` because the x86-64 kernel runs under TCG there.

To check the formatting of the Nix files alone, pass their paths to the shared formatter script:

```bash
./tools/nixfmt.sh --check flake.nix tools/dev_env/nix
```
