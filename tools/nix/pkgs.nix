# SPDX-License-Identifier: MPL-2.0
#
# The nixpkgs locked in flake.lock. Every Nix entry point in the repository
# instantiates nixpkgs through this file.
{
  system ? builtins.currentSystem,
  crossSystem ? null,
  overlays ? [ ],
  sources ? import ./sources.nix,
}:
import sources.nixpkgs {
  inherit system crossSystem overlays;
  config = { };
}
