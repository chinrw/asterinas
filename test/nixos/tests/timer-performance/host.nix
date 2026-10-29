# SPDX-License-Identifier: MPL-2.0
let
  pkgs = import ../../../../distro/nixpkgs.nix { };
in
pkgs.symlinkJoin {
  name = "timer-perf-host-clients";
  paths = [ pkgs.apacheHttpd pkgs.redis ];
}
