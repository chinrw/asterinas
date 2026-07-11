# SPDX-License-Identifier: MPL-2.0
#
# typos pinned to the version osdk/tools/docker/Dockerfile installs with
# `cargo install typos-cli@1.39.0`.
#
# Vendor dependencies from the upstream Cargo.lock (like klint.nix) instead
# of a cargoHash: `.overrideAttrs` does not re-derive cargoDeps from
# cargoHash, and nixpkgs' fetchCargoVendor path fails against crates.io
# without a User-Agent anyway (see klint.nix for the same issue).
#
# typos-Cargo.lock is copied from the pinned tag so evaluation does not
# need to fetch `src` first. Refresh it when bumping the version.
{ typos, fetchFromGitHub, rustPlatform }:

typos.overrideAttrs (old: rec {
  version = "1.39.0";
  src = fetchFromGitHub {
    owner = "crate-ci";
    repo = "typos";
    tag = "v${version}";
    hash = "sha256-S4toajgpKtPfvr6hhXE59lt0HPDHK/hF5vJJtxR0lTM=";
  };

  cargoDeps = rustPlatform.importCargoLock { lockFile = ./typos-Cargo.lock; };

  # typos' CLI snapshot tests (trycmd) compare against output captured
  # outside the Nix sandbox and fail here on formatting differences alone;
  # `typos --version` is still checked post-install below.
  doCheck = false;

  # Fail with a clear message when the committed lock copy goes stale.
  postPatch = (old.postPatch or "") + ''
    if ! diff -q ${./typos-Cargo.lock} Cargo.lock > /dev/null; then
      echo "error: nix/packages/typos-Cargo.lock does not match the pinned typos version." >&2
      echo "Copy Cargo.lock from the new version over nix/packages/typos-Cargo.lock." >&2
      exit 1
    fi
  '';
})
