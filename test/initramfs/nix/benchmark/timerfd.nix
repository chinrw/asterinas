{ stdenv }:
stdenv.mkDerivation {
  pname = "timerfd-bench";
  version = "0.1.0";
  src = ../../src/benchmark/timerfd;

  buildPhase = ''
    runHook preBuild

    ${stdenv.cc.targetPrefix}cc -O2 -std=c11 -Wall -Wextra timerfd_bench.c -o timerfd-bench

    runHook postBuild
  '';
  installPhase = ''
    runHook preInstall

    mkdir -p $out/bin
    cp timerfd-bench $out/bin/

    runHook postInstall
  '';
}
