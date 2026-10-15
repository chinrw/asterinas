# SPDX-License-Identifier: MPL-2.0
#
# Development tools shared by the OSDK image and the development shell.
# The image installs this bundle, so changing it changes both environments.
{
  buildEnv,
  cargo-binutils,
  cargo-udeps,
  mdbook,
  mdbook-mermaid,
  typos,
}:
buildEnv {
  name = "asterinas-dev-tools";
  paths = [
    cargo-binutils
    cargo-udeps
    mdbook
    mdbook-mermaid
    typos
  ];
  pathsToLink = [ "/bin" ];
}
