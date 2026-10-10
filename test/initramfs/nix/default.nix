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

  guestPkgs = import ../../../distro/nixpkgs.nix {
    config = { };
    overlays = [ ];
    inherit system crossSystem;
  };

  # Benchmark clients run on the build host for every guest target.
  hostPkgs = guestPkgs.pkgsBuildBuild;
  packages = import ./packages.nix;
in
(packages.guest {
  inherit
    guestPkgs
    enableBenchmarkTest
    enableConformanceTest
    enableRegressionTest
    conformanceTestSuite
    conformanceTestWorkDir
    conformanceTestSelector
    regressionTestPlatform
    dnsServer
    smp
    initramfsCompressed
    benchmarkName
    ;
})
// packages.host { inherit hostPkgs; }
