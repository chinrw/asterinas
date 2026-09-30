# Host-side clients of the network benchmarks. test/initramfs/Makefile installs
# them in the Docker image, and the Nix dev shell adds them to its PATH.
pkgs: {
  apacheHttpd = pkgs.apacheHttpd;
  iperf3 = pkgs.iperf3;
  libmemcached = pkgs.libmemcached.overrideAttrs (_: {
    configureFlags = [ "--enable-memaslap" ];
    LDFLAGS = "-lpthread";
    CPPFLAGS = "-fcommon -fpermissive";
  });
  lmbench = pkgs.callPackage ./benchmark/lmbench.nix { };
  redis =
    (pkgs.redis.overrideAttrs (old: {
      doCheck = false;
      makeFlags = (old.makeFlags or [ ]) ++ [
        "CC=${pkgs.stdenv.cc.targetPrefix}cc"
        "LD=${pkgs.stdenv.cc.targetPrefix}cc"
      ];
    })).override
      { withSystemd = false; };
}
