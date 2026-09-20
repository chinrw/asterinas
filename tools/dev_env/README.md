# Development environments

This directory contains the Docker and Nix configurations for Asterinas development.

- [Docker](docker/README.md) documents the development images and how to build them.
- [Nix](nix/README.md) documents the optional Linux development shell.

The Docker images build on `asterinas/osdk-dev`,
whose configuration remains in [osdk/tools/docker](../../osdk/tools/docker/README.md)
because it also supports development of other OSDK-based kernels.
The Nix shell provides the dependencies for building and using OSDK;
it does not require a separate environment directory under `osdk/`.

Keep shared dependency versions aligned when changing either environment.
The Nix packages identify their Dockerfile counterparts next to the version pins.
