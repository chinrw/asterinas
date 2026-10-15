# SPDX-License-Identifier: MPL-2.0
#
# Evaluates to the `.drv` paths of the benchmark clients
# that `make install_host_pkgs` installs on `system`.
# Evaluation fails if a client is not built for `system`
# or changes with the guest target,
# because the clients run on the build machine.
#
# A regression test for https://github.com/asterinas/asterinas/issues/4012,
# where `make install_host_pkgs` built x86_64 clients on an ARM64 host.
{ system }:
let
  clients = [
    "apacheHttpd"
    "iperf3"
    "libmemcached"
    "lmbench"
    "redis"
  ];
  targets = [
    "x86_64"
    "riscv64"
    "aarch64"
  ];
  # `make install_host_pkgs` passes no target, so it installs the default target's clients.
  installed = import ./. { inherit system; };
  checkClient =
    name:
    let
      drv = installed.${name};
      builtFor = drv.stdenv.hostPlatform.system;
      sameForTarget = target: (import ./. { inherit system target; }).${name}.drvPath == drv.drvPath;
    in
    if builtFor != system then
      throw "`${name}` is built for ${builtFor}, not ${system}"
    else if !builtins.all sameForTarget targets then
      throw "`${name}` changes with the guest target on ${system}"
    else
      drv.drvPath;
in
map checkClient clients
