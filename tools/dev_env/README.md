# Development environments

This directory contains the Docker and Nix configurations for Asterinas development.

- [Docker](docker/README.md) documents the development images and how to build them.
- [Using Nix for Development](../../book/src/kernel/nix-development.md) explains how to use the optional Linux development shell.
- [Maintaining the Nix Development Environment](nix/README.md) describes its implementation and dependency updates.

The Docker images build on `asterinas/osdk-dev`,
whose configuration remains in [osdk/tools/docker](../../osdk/tools/docker/README.md)
because it also supports development of other OSDK-based kernels.
The Nix shell provides the dependencies for building and using OSDK;
it does not require a separate environment directory under `osdk/`.

Keep shared dependency versions aligned when changing either environment.
The Nix packages identify their Dockerfile counterparts next to the version pins.
