# SPDX-License-Identifier: MPL-2.0
{
  pkgs,
  root ? ../../..,
}:
let
  sources = import ./sources.nix {
    inherit (pkgs) lib;
    inherit root;
  };
  cargoNix = import ./generated/Cargo.nix {
    inherit pkgs;
    release = false;
    buildRustCrateForPkgs =
      p:
      (p.buildRustCrate.override {
        rustc = pkgs.asterinas-rust-toolchain;
        cargo = pkgs.asterinas-rust-toolchain;
        defaultCodegenUnits = 256;
      });
    defaultCrateOverrides =
      pkgs.defaultCrateOverrides
      // builtins.mapAttrs (_: src: _: {
        inherit src;
      }) sources;
  };
in
{
  host-probe = cargoNix.workspaceMembers.ostd-pod.build.lib;
  inherit cargoNix;
}
