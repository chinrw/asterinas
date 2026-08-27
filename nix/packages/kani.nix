# SPDX-License-Identifier: MPL-2.0
{ lib, stdenv, fetchzip, makeWrapper, autoPatchelfHook, kaniRustToolchain, z3
, cvc5 }:

let
  version = "0.67.0";
  platforms = {
    x86_64-linux = {
      target = "x86_64-unknown-linux-gnu";
      hash = "sha256-I+GKPEWYXPZimCN79IB9dKiY8+NhP4Y8JjAS7R00XMs=";
    };
    aarch64-linux = {
      target = "aarch64-unknown-linux-gnu";
      hash = "sha256-rmXkmVwnkcp89PBkORzqpGzT7O7XwP3qGUEJ2cBhxkE=";
    };
    aarch64-darwin = {
      target = "aarch64-apple-darwin";
      hash = "sha256-6Rx985FWzTyKst0TcapUc6+d+SwvfhvfrNrTb24gUSE=";
    };
  };
  platform = platforms.${stdenv.hostPlatform.system} or (throw
    "Kani is unsupported on ${stdenv.hostPlatform.system}");
  runtimePath = lib.makeBinPath [ kaniRustToolchain z3 cvc5 ];
in stdenv.mkDerivation {
  pname = "kani";
  inherit version;

  src = fetchzip {
    url =
      "https://github.com/model-checking/kani/releases/download/kani-${version}/kani-${version}-${platform.target}.tar.gz";
    inherit (platform) hash;
  };

  dontUnpack = true;
  dontBuild = true;

  nativeBuildInputs = [ makeWrapper ]
    ++ lib.optionals stdenv.isLinux [ autoPatchelfHook ];
  buildInputs =
    lib.optionals stdenv.isLinux [ stdenv.cc.cc.lib kaniRustToolchain ];

  installPhase = ''
    runHook preInstall

    mkdir -p "$out/bin" "$out/libexec/kani"
    cp -R "$src"/. "$out/libexec/kani/"
    chmod -R u+w "$out/libexec/kani"
    ln -s ${kaniRustToolchain} "$out/libexec/kani/toolchain"

    makeWrapper "$out/libexec/kani/bin/kani-driver" "$out/bin/kani" \
      --argv0 kani \
      --prefix PATH : "$out/libexec/kani/bin:${runtimePath}"
    makeWrapper "$out/libexec/kani/bin/kani-driver" "$out/bin/cargo-kani" \
      --argv0 cargo-kani \
      --prefix PATH : "$out/libexec/kani/bin:${runtimePath}"

    for tool in cbmc goto-analyzer goto-cc goto-instrument kani-cov kissat; do
      ln -s "../libexec/kani/bin/$tool" "$out/bin/$tool"
    done

    runHook postInstall
  '';

  meta = {
    description = "Bit-precise model checker for Rust";
    homepage = "https://model-checking.github.io/kani/";
    license = with lib.licenses; [ asl20 mit ];
    mainProgram = "kani";
    platforms = builtins.attrNames platforms;
  };
}
