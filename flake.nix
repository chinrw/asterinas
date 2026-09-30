# SPDX-License-Identifier: MPL-2.0
{
  description = "Asterinas development environment";

  nixConfig = {
    extra-substituters = [
      "https://aster-nixos-release.cachix.org"
      "https://aster-nixos-dev.cachix.org"
    ];
    extra-trusted-public-keys = [
      "aster-nixos-release.cachix.org-1:xB6U/f5ck5vGDJZ04kPp3zGpZ4Nro9X4+TSSMAETVFE="
      "aster-nixos-dev.cachix.org-1:xrCbE2flfliFTQCY/2HeJoT2tCO+5kMTZeLIUH9lnIA="
    ];
  };

  inputs = {
    # flake.lock records the revision. Non-flake builds read it through
    # tools/nix/sources.nix.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { nixpkgs, ... }@inputs:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs systems (
          system:
          f (
            import ./. {
              inherit system;
              sources = inputs;
            }
          )
        );
    in
    {
      # rust-overlay is composed in so the overlay is usable on its own.
      overlays.default = nixpkgs.lib.composeManyExtensions (import ./. { sources = inputs; }).overlays;

      devShells = forAllSystems (repo: {
        default = repo.devShell;
      });

      packages = forAllSystems (repo: repo.packages);
    };
}
