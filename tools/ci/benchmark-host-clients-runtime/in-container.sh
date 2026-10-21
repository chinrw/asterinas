#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
exec > >(tee -a /evidence/stages.log) 2>&1
export PS4='+ $(date -u +%FT%TZ) ${BASH_SOURCE}:${LINENO}: '
set -x
test -f /.dockerenv
test "$(uname -m)" = "$VALIDATION_EXPECTED_ARCH"
uname -a > /evidence/container-uname.txt
cat /etc/os-release > /evidence/container-os-release.txt
findmnt -T /nix/store > /evidence/nix-store-mount.txt
cat /proc/self/mountinfo > /evidence/container-mountinfo.txt
if test -f /etc/profile.d/nix.sh; then source /etc/profile.d/nix.sh; fi
nix_tool_dir=$(dirname "$(readlink -f "$(command -v nix-env)")")
export NIX_REMOTE=local
export NIX_CONFIG=$'sandbox = false\nmax-jobs = 4\ncores = 4\nconnect-timeout = 30\nstalled-download-timeout = 120'
nix-env --version > /evidence/nix-version.txt
nix-env -q --out-path > /evidence/image-profile-before.txt
readlink -f /root/.nix-profile > /evidence/image-profile-link.txt
# Use a separate empty profile in this disposable container. This avoids old
# packages baked into the development image colliding with the candidate.
nix-env --switch-profile /nix/var/nix/profiles/host-client-validation
export PATH="/root/.nix-profile/bin:$nix_tool_dir:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
nix-env -q --out-path > /evidence/test-profile-before.txt
test ! -s /evidence/test-profile-before.txt
cat > /tmp/selected-clients.nix <<'NIX'
let
  installed = import /source/test/initramfs/nix/default.nix {};
  names = [ "apacheHttpd" "iperf3" "libmemcached" "lmbench" "redis" ];
in builtins.listToAttrs (map (name: let p = installed.${name}; in {
  inherit name;
  value = {
    drvPath = p.drvPath;
    outputPath = p.outPath;
    build = p.stdenv.buildPlatform.system;
    host = p.stdenv.hostPlatform.system;
    target = p.stdenv.targetPlatform.system;
  };
}) names)
NIX
timeout 600 nix-instantiate --eval --strict --json /tmp/selected-clients.nix > /evidence/selected-clients.json
set +e
timeout 3600 make -C /source/test/initramfs install_host_pkgs 2>&1 | tee /evidence/install-host-pkgs.log
install_status=${PIPESTATUS[0]}
set -e
printf '%s\n' "$install_status" > /evidence/install-exit-code.txt
test "$install_status" -eq 0
nix-env -q --out-path > /evidence/test-profile-after-install.txt
nix-env -q --json > /evidence/test-profile-after-install.json
# The server is a support dependency; the five clients still come exclusively
# from the original installer and are invoked through that profile.
timeout 1200 nix-build /source/distro/nixpkgs.nix -A memcached --out-link /tmp/host-client-support-memcached > /evidence/memcached-server-output.txt 2> /evidence/memcached-server-build.log
export VALIDATION_MEMCACHED_SERVER=/tmp/host-client-support-memcached/bin/memcached
set +e
python3 /harness/functional.py before-gc
before_status=$?
set -e
printf '%s\n' "$before_status" > /evidence/before-gc-exit-code.txt
ps -ef > /evidence/processes-before-gc.txt
set +e
timeout 1200 make -C /source/test/initramfs nix_gc > /evidence/nix-gc.log 2>&1
gc_status=$?
tail -n 30 /evidence/nix-gc.log
set -e
printf '%s\n' "$gc_status" > /evidence/gc-exit-code.txt
nix-env -q --out-path > /evidence/test-profile-after-gc.txt
set +e
python3 /harness/functional.py after-gc
after_status=$?
set -e
printf '%s\n' "$after_status" > /evidence/after-gc-exit-code.txt
ps -ef > /evidence/processes-after-gc.txt
test "$before_status" -eq 0
test "$gc_status" -eq 0
test "$after_status" -eq 0
