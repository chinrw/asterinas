# SPDX-License-Identifier: MPL-2.0
#
# The flake inputs, fetched at the revisions in flake.lock for non-flake callers
# such as `nix-build`. The narHash in the lock equals the hash of the unpacked
# tarball, so this yields the same store paths as the flake.
let
  lock = builtins.fromJSON (builtins.readFile ../../flake.lock);
  fetchNode =
    name: nodeName:
    let
      locked = lock.nodes.${nodeName}.locked;
    in
    if locked.type == "github" then
      builtins.fetchTarball {
        url = "https://github.com/${locked.owner}/${locked.repo}/archive/${locked.rev}.tar.gz";
        sha256 = locked.narHash;
      }
    else
      throw "tools/nix/sources.nix cannot fetch flake input '${name}' of type '${locked.type}'";
in
builtins.mapAttrs fetchNode lock.nodes.root.inputs
