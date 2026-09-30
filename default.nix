# SPDX-License-Identifier: MPL-2.0
#
# The development environment and boot tools. flake.nix passes its own inputs
# as `sources`, and `nix-build` falls back to the revisions in flake.lock.
{
  system ? builtins.currentSystem,
  sources ? import ./tools/nix/sources.nix,
}:
let
  overlays = [
    (import sources.rust-overlay)
    (import ./tools/nix/overlay.nix)
  ];
  pkgs = import ./tools/nix/pkgs.nix { inherit system overlays sources; };
in
{
  inherit overlays;

  devShell = pkgs.callPackage ./tools/dev_env/nix/devshell.nix { };

  packages = {
    qemu = pkgs.asterinas-qemu;
    grub = pkgs.asterinas-grub;
    ovmf = pkgs.asterinas-ovmf;
  };
}
