# SPDX-License-Identifier: MPL-2.0
#
# Host-side clients of the network benchmarks.
# They run on the build machine,
# so `pkgs` must be a package set built for the build machine.
#
# `make install_host_pkgs` and the development shell both call this file.
# They evaluate to the same store paths
# only while both pass a package set without configuration or overlays
# from the nixpkgs revision locked in `flake.lock`.
# `--override-input` breaks that match for the shell.
# The "Check benchmark clients" step of the Test Nix flake workflow checks the match.
{ pkgs }:
{
  apacheHttpd = pkgs.apacheHttpd;
  iperf3 = pkgs.iperf3;
  libmemcached = pkgs.libmemcached.overrideAttrs (_: {
    configureFlags = [ "--enable-memaslap" ];
    LDFLAGS = "-lpthread";
    CPPFLAGS = "-fcommon -fpermissive";
  });
  lmbench = pkgs.callPackage ./benchmark/lmbench.nix { };
  redis =
    (pkgs.redis.overrideAttrs (_: {
      doCheck = false;
    })).override
      { withSystemd = false; };
}
