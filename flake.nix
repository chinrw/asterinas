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
    crate2nix-src = {
      url = "github:nix-community/crate2nix/7c33e664668faecf7655fa53861d7a80c9e464a2";
      flake = false;
    };
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
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
      crate2nix-src,
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

      packages = forAllSystems (
        pkgs:
        {
          c2n-exp-generator = pkgs.callPackage (crate2nix-src + "/default.nix") { };
          c2n-exp-toolchain = pkgs.asterinas-rust-toolchain;
          c2n-exp-cachix = pkgs.cachix;
          qemu = pkgs.asterinas-qemu;
          grub = pkgs.asterinas-grub;
          ovmf = pkgs.asterinas-ovmf;
        }
        // pkgs.lib.optionalAttrs (pkgs.stdenv.hostPlatform.system == "x86_64-linux") (
          let
            experiment = import ./tools/experiments/crate2nix/experiment.nix { inherit pkgs; };
          in
          {
            c2n-exp-host-probe = experiment.host-probe;
            c2n-exp-sysroot-x86_64 = experiment.sysroot;
            c2n-exp-kernel-dev = experiment.kernel-dev;
            c2n-exp-integration-initramfs = experiment.integration-initramfs;
          }
        )
      );

      legacyPackages = forAllSystems (
        pkgs:
        pkgs.lib.optionalAttrs (pkgs.stdenv.hostPlatform.system == "x86_64-linux") {
          c2n-exp = import ./tools/experiments/crate2nix/experiment.nix { inherit pkgs; };
        }
      );
    };
}
