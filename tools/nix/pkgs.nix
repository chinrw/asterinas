# SPDX-License-Identifier: MPL-2.0
#
# The nixpkgs locked in flake.lock, shared by the test images and AsterNixOS.
{
  system ? builtins.currentSystem,
  crossSystem ? null,
  sources ? import ./sources.nix,
}:
import sources.nixpkgs {
  inherit system crossSystem;
  config = { };
  overlays = [ ];
}
