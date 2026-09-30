{
  target ? "x86_64",
  enableBenchmarkTest ? false,
  enableConformanceTest ? false,
  enableRegressionTest ? false,
  conformanceTestSuite ? "ltp",
  conformanceTestWorkDir ? "/tmp",
  conformanceTestSelector ? "",
  regressionTestPlatform ? "asterinas",
  dnsServer ? "none",
  smp ? 1,
  initramfsCompressed ? true,
  benchmarkName ? "none",
  system ? builtins.currentSystem,
}:
let
  crossSystem.config =
    if target == "x86_64" then
      "x86_64-unknown-linux-gnu"
    else if target == "riscv64" then
      "riscv64-unknown-linux-gnu"
    else if target == "aarch64" then
      "aarch64-unknown-linux-gnu"
    else
      throw "Target arch ${target} not yet supported.";

  pkgs = import ../../../tools/nix/pkgs.nix { inherit system crossSystem; };
  # The benchmark clients run on the host that drives the guest, so they are
  # built natively instead of for the target.
  hostTools = import ./host-tools.nix (import ../../../tools/nix/pkgs.nix { inherit system; });
in
rec {
  # Packages needed by initramfs
  busybox = pkgs.busybox;
  benchmark = pkgs.callPackage ./benchmark { inherit benchmarkName; };
  conformance = pkgs.callPackage ./conformance {
    inherit smp;
    testSuite = conformanceTestSuite;
    workDir = conformanceTestWorkDir;
    testSelector = conformanceTestSelector;
  };
  regression = pkgs.callPackage ./regression { testPlatform = regressionTestPlatform; };

  initramfs = pkgs.callPackage ./initramfs.nix {
    inherit busybox;
    benchmark = if enableBenchmarkTest then benchmark else null;
    conformance = if enableConformanceTest then conformance else null;
    regression = if enableRegressionTest then regression else null;
    dnsServer = dnsServer;
  };
  initramfs-image = pkgs.callPackage ./initramfs-image.nix {
    inherit initramfs;
    compressed = initramfsCompressed;
  };
  rootfs-image = pkgs.callPackage ./rootfs-image.nix { inherit initramfs; };

  # Packages that the prebuilt Docker image builds ahead of time,
  # so that CI workflows do not build them again.
  prebuilt = [
    busybox
    conformance.ltp
    regression.package
  ]
  ++ pkgs.lib.optionals (target == "x86_64") [
    benchmark.fio
    benchmark.hackbench
    benchmark.iperf3
    benchmark.lmbench
    benchmark.memcached
    benchmark.nginx
    benchmark.redis
    benchmark.schbench
    benchmark.sqlite-speedtest1
    benchmark.sysbench
    conformance.kselftest
  ];

  # Packages needed by host
  inherit (hostTools)
    apacheHttpd
    iperf3
    libmemcached
    lmbench
    redis
    ;
}
