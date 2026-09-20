# Using Nix for Development

You can use a Nix development shell to build and run Asterinas without entering a Docker container.
The shell provides the Rust toolchain, QEMU, firmware, and host build tools.
Run the commands below from the root of your Asterinas checkout.
If you have not cloned the repository, follow the first step in [Getting Started](README.md).

## Prerequisites

This guide assumes that [Nix is installed](https://nixos.org/download/) on an x86-64 Linux host.
Enable the `nix-command` and `flakes` experimental features in your Nix configuration,
for example by adding the following line to `~/.config/nix/nix.conf`:

```text
experimental-features = nix-command flakes
```

The flake also provides an `aarch64-linux` shell.
Its output is checked by Nix evaluation, but the flake CI's build and boot test runs on x86-64 Linux.
The host architecture does not select the kernel architecture:
the Makefile defaults to an x86-64 kernel on both hosts.
macOS is not supported by this development shell.

On NixOS, enable envfs in your host configuration and rebuild it before using the shell:

```nix
services.envfs.enable = true;
```

The build scripts invoke `/bin/bash`, which envfs resolves using the calling process's `PATH`.

QEMU uses KVM by default.
Your user must have access to `/dev/kvm` to use hardware acceleration.
If KVM is unavailable, add `ENABLE_KVM=0` to the run command to use slower software emulation.

## Build and run Asterinas

Enter the development shell:

```bash
nix develop
```

The first invocation downloads dependencies and may build packages that are not available from a binary cache.
It can take substantially longer than subsequent invocations, which reuse the local Nix store.
The flake does not configure a project-specific binary cache.

Build the kernel and start it in QEMU:

```bash
make kernel
make run_kernel
```

The Makefile installs or updates `cargo-osdk` from the checkout when needed.
For an automated boot check that exits after the guest reports success, run:

```bash
make run_kernel AUTO_TEST=boot
```

To check formatting, Rust code, and spelling, run:

```bash
make check
```

You can also run a command without entering an interactive shell:

```bash
nix develop --command make check
```

## Editors and direnv

The shell includes `rust-analyzer` from the same nightly as the Rust toolchain.
Start your editor from inside the shell so that it inherits the toolchain and `VDSO_LIBRARY_DIR`.
For example, if VS Code is installed on your host:

```bash
nix develop
code .
```

If you use [direnv](https://direnv.net/) with its shell hook enabled,
the repository's `.envrc` can activate the environment when you enter the checkout.
Run `direnv allow` from the repository root to approve it.
If a later change modifies `.envrc`, direnv requires approval again.

## Tests and current limitations

The shell supplies the tools needed to build test images.
It does not install every test suite and benchmark when you run `nix develop`.
The initramfs build uses existing Nix definitions to obtain the selected tests and their runtime dependencies on demand.
See [Advanced Build and Test Instructions](advanced-instructions.md) for the test entry points.

The gVisor syscall tests are an exception.
The development Docker image builds their binaries with Bazel,
and the Nix initramfs definition imports them through `GVISOR_PREBUILT_DIR`.
It also copies shared libraries from `/lib/x86_64-linux-gnu` in the build environment.
Setting `GVISOR_PREBUILT_DIR` alone does not provide those libraries on a different host.
Use the Docker environment for the existing gVisor workflow.

Projects created by `cargo osdk new` and the OSDK test suite's TDX scheme still use Docker-specific firmware paths.
The development shell does not make those configurations portable automatically.
The shared QEMU scripts also do not handle firmware paths containing spaces correctly.
The shell's default firmware path is in the Nix store and contains no spaces;
avoid overriding `OVMF_DIR` with a path containing spaces.
