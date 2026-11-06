# SPDX-License-Identifier: MPL-2.0
{ pkgs }:
let
  host = import ./default.nix { inherit pkgs; };
  kernel = import ./kernel.nix { inherit pkgs; };
in
{
  inherit (host) host-probe;
  inherit (kernel) kernel-dev sysroot;
  graph = import ./graph.nix kernel.kernel-dev;
  host-graph = import ./graph.nix host.host-probe;
  integration-initramfs = import ./integration-initramfs.nix { inherit pkgs; };
}
