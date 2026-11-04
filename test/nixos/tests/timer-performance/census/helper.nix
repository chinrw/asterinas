# SPDX-License-Identifier: MPL-2.0
{ pkgs }:
pkgs.stdenv.mkDerivation {
  pname = "timer-census";
  version = "1";
  dontUnpack = true;
  buildPhase = ''
    $CC -std=gnu11 -O2 -Wall -Wextra -Werror ${./census_probe.c} -o timer-census
    $CC -std=gnu11 -O2 -Wall -Wextra -Werror ${./workload.c} -o census-workload
  '';
  installPhase = ''
    mkdir -p $out/bin
    cp timer-census census-workload $out/bin/
  '';
}
