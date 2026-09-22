# SPDX-License-Identifier: MPL-2.0
{
  description = "Asterinas development environment";

  # Keep this list in sync with the Cachix settings in Makefile, which use the
  # same two caches for the AsterNixOS builds. Only the development cache holds
  # the packages this flake builds.
  nixConfig = {
    extra-substituters = [
      "https://aster-nixos-release.cachix.org"
      "https://asterina-test.cachix.org"
    ];
    extra-trusted-public-keys = [
      "aster-nixos-release.cachix.org-1:xB6U/f5ck5vGDJZ04kPp3zGpZ4Nro9X4+TSSMAETVFE="
      "asterina-test.cachix.org-1:LUjQ7OX2Ur+VC605+JQbI1wGyYTHN90RdmICRwc4K8M="
    ];
  };

  inputs = {
    # Keep Nix-based builds on the nixpkgs revision the rest of the repository
    # pins: tools/dev_env/docker/prebuilt-nix-packages/Dockerfile and
    # test/initramfs/nix/default.nix.
    nixpkgs.url = "github:NixOS/nixpkgs/fd1462031fdee08f65fd0b4c6b64e22239a77870";
    # Match typos 1.39.0 from osdk/tools/docker/Dockerfile.
    nixpkgs-typos.url = "github:NixOS/nixpkgs/c5ae371f1a6a7fd27823bc500d9390b38c05fa55";
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      nixpkgs-typos,
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
        default = pkgs.callPackage ./tools/dev_env/nix/devshell.nix {
          typos = nixpkgs-typos.legacyPackages.${pkgs.stdenv.hostPlatform.system}.typos;
        };
      });

      packages = forAllSystems (pkgs: {
        qemu = pkgs.asterinas-qemu;
        grub = pkgs.asterinas-grub;
        ovmf = pkgs.asterinas-ovmf;
      });
    };
}
