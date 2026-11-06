# SPDX-License-Identifier: MPL-2.0
{
  pkgs,
  lib,
  defaultCrateOverrides,
  toolchain,
  sysroot,
}:
crate:
let
  target = pkgs.stdenv.hostPlatform.parsed.kernel.name == "none";
  contract = builtins.fromJSON (builtins.readFile ./generated/build-contract.json);
  featureNames = builtins.fromJSON (builtins.readFile ./generated/feature-names.json);
  normalizedCrate = crate // {
    features = builtins.filter (
      feature: builtins.elem feature featureNames."${crate.crateName}@${crate.version}"
    ) crate.features;
  };
  builder = pkgs.buildRustCrate.override {
    inherit defaultCrateOverrides;
    rustc = toolchain;
    cargo = toolchain;
    defaultCodegenUnits = 16;
    stdenv = if target then pkgs.stdenvNoCC else pkgs.stdenv;
  };
  flags = [
    "-C embed-bitcode=no"
  ]
  ++ lib.optionals target [
    "--sysroot ${sysroot}"
  ]
  ++ lib.optionals target contract.library_rustflags
  ++ lib.optionals (target && crate.crateName == "asterinas-osdk-bin") contract.final_link_flags;
  cfgFlags = lib.concatStringsSep " " (
    builtins.filter (flag: lib.hasPrefix "-C target-feature=" flag) contract.library_rustflags
  );
in
((builder normalizedCrate).override { extraRustcOpts = flags; }).overrideAttrs (old: {
  # The target has no C runtime. Keep its static-link setting out of the
  # native C linker used to link build-script executables.
  env = (old.env or { }) // {
    NIX_CFLAGS_LINK = "";
  };
  passthru = (old.passthru or { }) // {
    c2nRole = if target then "target" else "host";
  };
  # buildRustCrate hard-codes GNU/Linux cfg values. Build scripts must see
  # the cfg of their compilation target, including the bare-metal target.
  configurePhase =
    builtins.replaceStrings
      [ "# Compile and run the build script, when available." ]
      [
        ''
          for cfg_var in ''${!CARGO_CFG_@}; do
            unset "$cfg_var"
          done
          while IFS='=' read -r key value; do
            cfg_var="CARGO_CFG_''${key^^}"
            value="''${value//\"/}"
            if [ -n "''${!cfg_var+x}" ] && [ -n "''${!cfg_var}" ]; then
              value="''${!cfg_var},$value"
            fi
            export "$cfg_var=$value"
          done < <(rustc --print cfg --target "$TARGET" ${lib.optionalString target cfgFlags})
          # Compile and run the build script, when available.
        ''
      ]
      old.configurePhase;
})
