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
    {
      self,
      nixpkgs,
      rust-overlay,
    }:
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
            import nixpkgs {
              inherit system;
              overlays = [ self.overlays.default ];
            }
          )
        );
    in
    {
      # rust-overlay is composed in so the overlay is usable on its own.
      overlays.default = nixpkgs.lib.composeExtensions (import rust-overlay) (
        import ./tools/dev_env/nix/overlay.nix
      );

      devShells = forAllSystems (pkgs: {
        default = pkgs.callPackage ./tools/dev_env/nix/devshell.nix { };
      });

      packages = forAllSystems (pkgs: {
        qemu = pkgs.asterinas-qemu;
        grub = pkgs.asterinas-grub;
        ovmf = pkgs.asterinas-ovmf;
      });
    };
}
