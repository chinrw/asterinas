# SPDX-License-Identifier: MPL-2.0
{ pkgs }:
let
  toolchain = pkgs.asterinas-rust-toolchain;
  contract = builtins.fromJSON (builtins.readFile ./generated/build-contract.json);
  cargoDeps = pkgs.rustPlatform.importCargoLock {
    lockFile = ./generated/rust-sysroot.lock;
  };
in
pkgs.stdenv.mkDerivation {
  name = "c2n-exp-sysroot-x86_64-dev";
  src = ./sysroot-fixture;
  nativeBuildInputs = [ toolchain ];
  configurePhase = ''
    export CARGO_HOME="$TMPDIR/cargo-home"
    mkdir -p "$CARGO_HOME"
    cat > "$CARGO_HOME/config.toml" <<EOF
    [source.crates-io]
    replace-with = "vendored-sources"
    [source.vendored-sources]
    directory = "${cargoDeps}"
    EOF
  '';
  RUSTFLAGS = pkgs.lib.concatStringsSep " " (
    contract.library_rustflags
    ++ [
      "--remap-path-prefix=${toolchain}=/rustc"
    ]
  );
  buildPhase = ''
    cargo build --offline -vv -j "$NIX_BUILD_CORES" \
      --target x86_64-unknown-none \
      ${pkgs.lib.escapeShellArgs contract.build_std_args}
  '';
  installPhase = ''
    mkdir -p "$out/lib/rustlib/x86_64-unknown-none/lib"
    for name in core alloc compiler_builtins rustc_std_workspace_core; do
      for file in target/x86_64-unknown-none/debug/deps/lib"$name"-*.rlib; do
        if [ -f "$file" ]; then
          cp "$file" "$out/lib/rustlib/x86_64-unknown-none/lib/"
        fi
      done
    done
    cp Cargo.lock "$out/fixture-Cargo.lock"
  '';
  dontFixup = true;
}
