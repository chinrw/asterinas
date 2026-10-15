# SPDX-License-Identifier: MPL-2.0
#
# Host-side clients of the network benchmarks.
# They run on the build machine,
# so `pkgs` must be a package set built for the build machine.
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
