# SPDX-License-Identifier: MPL-2.0

{
  system ? builtins.currentSystem,
}:
assert system == "x86_64-linux";
let
  pkgs = import ../../../distro/nixpkgs.nix { inherit system; };
  inherit (pkgs) lib;

  kernelUrl = lib.removeSuffix "\n" (builtins.readFile ../src/benchmark/common/linux-kernel-url);
  kernel = pkgs.fetchurl {
    url = kernelUrl;
    hash = "sha256-HT6KiGY7ygemKqdpVZ9JFT3b3aEGKwziwdydd7BtJOc=";
  };
  config = pkgs.fetchurl {
    name = "linux-6.16.0.config";
    url = lib.removeSuffix "vmlinuz" kernelUrl + ".config";
    hash = "sha256-FElBiImIF4+F3krRvzjdqqOsDgsAiO1XcQM6J+uELKk=";
  };
  modules = pkgs.callPackage ./benchmark-linux-modules {
    stdenv = pkgs.gcc13Stdenv;
    inherit kernel config;
  };
  regression = pkgs.callPackage ./regression { testPlatform = "linux"; };
  initramfs = pkgs.callPackage ./initramfs.nix {
    busybox = pkgs.busybox;
    benchmark = null;
    conformance = null;
    inherit regression;
    dnsServer = "none";
  };

  guestTools = [
    pkgs.util-linux
    pkgs.iproute2
  ];
  hostTools = pkgs.symlinkJoin {
    name = "linux-regression-host-tools";
    paths = [
      (pkgs.callPackage ../../../tools/dev_env/nix/packages/qemu.nix { })
      pkgs.e2fsprogs
      pkgs.exfatprogs
      pkgs.coreutils
    ];
  };
in
pkgs.runCommand "linux-regression-6.16.0"
  {
    nativeBuildInputs = [
      pkgs.cpio
      pkgs.gzip
      pkgs.python3
    ];
  }
  ''
    mkdir -p $out root
    cp -r ${initramfs}/. root/
    chmod u+w root root/test root/etc root/usr/bin root/usr/lib root/nix/store

    # Tests create files and setuid helpers beside their executables.
    rm -rf root/test
    mkdir root/test
    cp -rL ${regression.package}/. root/test/
    chmod -R u+w root/test

    for dependency in $(cat ${pkgs.writeClosure guestTools}); do
      if [ ! -e "root$dependency" ]; then
        cp -r "$dependency" root/nix/store/
      fi
    done
    ln -sfn ${pkgs.util-linux}/bin/script root/usr/bin/script
    ln -sfn ${pkgs.iproute2}/bin/ip root/usr/bin/ip
    touch $out/subsystem-v1
    cp ${../src/regression/scripts/linux_init.sh} root/linux-init
    chmod 0755 root/linux-init
    cp ${config} root/etc/benchmark-linux.config
    cp -r ${modules}/lib/modules root/usr/lib/

    python3 ${./linux-regression-entries.py} root/test/*/run_test.sh

    printf 'group\tcase\taction\tcommand\treason\n' > $out/cases.tsv
    for directory in root/test/*; do
      [ -d "$directory" ] || continue
      group=$(basename "$directory")
      test -x "$directory/run_test.sh"
      echo "$group" >> $out/groups
      (
        cd "$directory"
        REGRESSION_TEST_MODE=list \
          REGRESSION_TEST_MANIFEST="$out/cases.tsv" \
          REGRESSION_TEST_ROOT="$NIX_BUILD_TOP/root/test" \
          REGRESSION_TEST_GROUP="$group" \
          REGRESSION_BUILTIN_CONFIG=${config} \
          REGRESSION_TEST_PLATFORM=linux \
          ${pkgs.runtimeShell} ./run_test.sh > /dev/null
      )
    done
    test -s $out/groups
    test "$(wc -l < $out/cases.tsv)" -gt 1

    chmod 0755 root root/test root/etc root/root root/var
    chmod 1777 root/tmp
    (cd root && find . -print0 | sort -z | cpio --quiet --null -o -H newc \
      --owner=0:0 --reproducible | gzip -n > $out/initramfs.cpio.gz)
    ln -s ${kernel} $out/bzImage
    ln -s ${config} $out/kernel.config
    ln -s ${hostTools} $out/host-tools
  ''
