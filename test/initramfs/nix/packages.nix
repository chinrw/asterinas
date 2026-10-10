# SPDX-License-Identifier: MPL-2.0
{
  guest =
    {
      guestPkgs,
      enableBenchmarkTest,
      enableConformanceTest,
      enableRegressionTest,
      conformanceTestSuite,
      conformanceTestWorkDir,
      conformanceTestSelector,
      regressionTestPlatform,
      dnsServer,
      smp,
      initramfsCompressed,
      benchmarkName,
    }:
    rec {
      busybox = guestPkgs.busybox;
      benchmark = guestPkgs.callPackage ./benchmark { inherit benchmarkName; };
      conformance = guestPkgs.callPackage ./conformance {
        inherit smp;
        testSuite = conformanceTestSuite;
        workDir = conformanceTestWorkDir;
        testSelector = conformanceTestSelector;
      };
      regression = guestPkgs.callPackage ./regression { testPlatform = regressionTestPlatform; };

      initramfs = guestPkgs.callPackage ./initramfs.nix {
        inherit busybox;
        benchmark = if enableBenchmarkTest then benchmark else null;
        conformance = if enableConformanceTest then conformance else null;
        regression = if enableRegressionTest then regression else null;
        dnsServer = dnsServer;
      };
      initramfs-image = guestPkgs.callPackage ./initramfs-image.nix {
        inherit initramfs;
        compressed = initramfsCompressed;
      };
      rootfs-image = guestPkgs.callPackage ./rootfs-image.nix { inherit initramfs; };
    };

  host =
    { hostPkgs }:
    {
      apacheHttpd = hostPkgs.apacheHttpd;
      iperf3 = hostPkgs.iperf3;
      libmemcached = hostPkgs.libmemcached.overrideAttrs (_: {
        configureFlags = [ "--enable-memaslap" ];
        LDFLAGS = "-lpthread";
        CPPFLAGS = "-fcommon -fpermissive";
      });
      lmbench = hostPkgs.callPackage ./benchmark/lmbench.nix { };
      redis =
        (hostPkgs.redis.overrideAttrs (old: {
          doCheck = false;
          makeFlags = (old.makeFlags or [ ]) ++ [
            "CC=${hostPkgs.stdenv.cc.targetPrefix}cc"
            "LD=${hostPkgs.stdenv.cc.targetPrefix}cc"
          ];
        })).override
          { withSystemd = false; };
    };
}
