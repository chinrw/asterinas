# SPDX-License-Identifier: MPL-2.0
{ pkgs }:
pkgs.runCommand "timer-batch-probe" { nativeBuildInputs = [ pkgs.gcc ]; } ''
  mkdir -p $out/bin
  gcc -std=gnu11 -O2 -Wall -Wextra -Werror ${./batch_probe.c} -o $out/bin/timer-batch-probe
''
