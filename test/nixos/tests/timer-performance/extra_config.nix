# SPDX-License-Identifier: MPL-2.0
{ pkgs, ... }:
let
  timerPerf = import ./timer_helper.nix { inherit pkgs; };
  sqliteSpeedtest = pkgs.callPackage ../../../initramfs/nix/benchmark/sqlite-speedtest1.nix { };
  redis = (pkgs.redis.overrideAttrs (_: { doCheck = false; })).override { withSystemd = false; };
  goHelper = import ./minimal_helper.nix { inherit pkgs; };
in
{
  environment.systemPackages = [ pkgs.nginx redis sqliteSpeedtest pkgs.python3 ];
  networking.firewall.enable = false;
  environment.etc."timer-perf/timer-perf".source = "${timerPerf}/bin/timer-perf";
  environment.etc."timer-perf/setup.py".source = ./setup.py;
  environment.etc."timer-perf/loadcheck.py".source = ./loadcheck.py;
  environment.etc."timer-perf/sqlite-speedtest1".source = "${sqliteSpeedtest}/bin/sqlite-speedtest1";
  environment.etc."notingo/minimal".source = "${goHelper}/bin/minimal-cgo";
}
