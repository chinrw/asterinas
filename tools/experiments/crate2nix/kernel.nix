# SPDX-License-Identifier: MPL-2.0
{
  pkgs,
  root ? ../../..,
  buildIdentity ? {
    user = "crate2nix-ci";
    host = "crate2nix-ci";
  },
}:
let
  sources = import ./sources.nix {
    inherit (pkgs) lib;
    inherit root;
  };
  contract = builtins.fromJSON (builtins.readFile ./generated/build-contract.json);
  sysroot = import ./sysroot.nix { inherit pkgs; };
  componentMetadata = import ./component-metadata.nix { inherit pkgs root; };
  members = builtins.fromJSON (builtins.readFile ./generated/source-map.json);
  componentNames = map (component: component.name) (
    builtins.fromJSON (builtins.readFile ./generated/components.json)
  );
  localOverrides = builtins.mapAttrs (
    name: src: _:
    {
      inherit src;
      codegenUnits = 256;
    }
    // pkgs.lib.optionalAttrs (builtins.elem name componentNames) {
      libPath = "${src}/src/lib.rs";
      preBuild = ''export CARGO_MANIFEST_DIR="${src}"'';
      extraRustcOpts = [ "--remap-path-prefix=${src}=${componentMetadata}/${members.${name}}" ];
    }
  ) sources;
  metadataCargo = pkgs.writeShellScript "component-cargo-metadata" ''
    set -eu
    test "$*" = "metadata --format-version 1"
    cat "$C2N_COMPONENT_METADATA"
  '';
  crossPkgs = import pkgs.path {
    localSystem = pkgs.stdenv.buildPlatform.system;
    crossSystem = {
      config = "x86_64-unknown-none-elf";
      isStatic = true;
      rust.rustcTarget = "x86_64-unknown-none";
    };
  };
  cargoNix = import ./generated/kernel-Cargo.nix {
    pkgs = crossPkgs;
    release = false;
    buildRustCrateForPkgs =
      p:
      p.callPackage ./builder.nix {
        toolchain = pkgs.asterinas-rust-toolchain;
        inherit sysroot;
      };
    defaultCrateOverrides =
      pkgs.defaultCrateOverrides
      // localOverrides
      // {
        component-macro =
          attrs:
          localOverrides.component-macro attrs
          // {
            CARGO = metadataCargo;
          };
        aster-core =
          attrs:
          localOverrides.aster-core attrs
          // {
            VDSO_LIBRARY_DIR = pkgs.asterinas-vdso;
            C2N_COMPONENT_METADATA = "${componentMetadata}/metadata.json";
            OSDK_BUILD_USERNAME = buildIdentity.user;
            OSDK_BUILD_HOSTNAME = buildIdentity.host;
            OSDK_BUILD_RUSTC = contract.rustc_version;
          };
        ostd =
          attrs:
          localOverrides.ostd attrs
          // {
            OSTD_TASK_STACK_SIZE_IN_PAGES = "64";
          };
        smoltcp = _: {
          # The pinned build script emits constant declarations by iterating a
          # HashMap. Preserve every declaration while fixing their source order.
          postConfigure = ''LC_ALL=C sort -o "$OUT_DIR/config.rs" "$OUT_DIR/config.rs"'';
        };
        aster-nvme =
          attrs:
          localOverrides.aster-nvme attrs
          // {
            C2N_COMPONENT_METADATA = "${componentMetadata}/metadata.json";
          };
      };
  };
in
assert pkgs.lib.assertMsg (contract.target_arch == "x86_64" && contract.profile == "dev")
  "The crate2nix experiment supports only the exported x86_64 dev build contract";
{
  kernel-dev = cargoNix.rootCrate.build;
  inherit cargoNix sysroot;
}
