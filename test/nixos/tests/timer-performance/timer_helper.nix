# SPDX-License-Identifier: MPL-2.0
{ pkgs }:
pkgs.runCommand "timer-performance-probe" { nativeBuildInputs = [ pkgs.gcc ]; } ''
  mkdir -p $out/bin
  gcc -O2 -Wall -Wextra -Werror ${./timer_perf.c} -o $out/bin/timer-perf
''
