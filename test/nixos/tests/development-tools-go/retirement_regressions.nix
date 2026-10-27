# SPDX-License-Identifier: MPL-2.0
{ pkgs }:
let
  source = ../../../initramfs/src/regression;
in
pkgs.runCommand "timer-retirement-regressions" { nativeBuildInputs = [ pkgs.gcc ]; } ''
  mkdir -p "$out/bin"
  for file in clock_nanosleep/nanosleep_err gettimeofday/gettimeofday itimer/setitimer itimer/timer_create timerfd/timerfd_clockid timerfd/timerfd_readiness; do
    name=$(basename "$file")
    gcc -O2 -pthread "${source}/time/$file.c" -lrt -o "$out/bin/$name"
  done
  gcc -O2 -pthread ${./retirement_nanosleep_bounds.c} -o "$out/bin/nanosleep_bounds"
''
