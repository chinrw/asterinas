# SPDX-License-Identifier: MPL-2.0

{
  system ? builtins.currentSystem,
}:
assert system == "x86_64-linux";
let
  kernelVersion = "6.16.0";
  pkgs = import ../../../distro/nixpkgs.nix { inherit system; };
  # Keep `enableRegressionTest = true` so `initramfs.nix` populates `root/nix/store`
  # with `regression.package`'s runtime shared-library closure (`writeClosure`).
  initramfsPkgs = import ./default.nix {
    target = "x86_64";
    enableRegressionTest = true;
    regressionTestPlatform = "linux";
    inherit system;
  };
  kernel = pkgs.fetchurl {
    name = "vmlinuz-${kernelVersion}";
    url = "https://raw.githubusercontent.com/asterinas/linux_binary_cache/8e6e8ad382aac7e605d7a058811d09bce478cd7d/${kernelVersion}/vmlinuz";
    hash = "sha256-HT6KiGY7ygemKqdpVZ9JFT3b3aEGKwziwdydd7BtJOc=";
  };
  inherit (initramfsPkgs) regression initramfs;

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
pkgs.runCommand "linux-regression-${kernelVersion}"
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
    chmod u+w root root/test

    # Replace `initramfs`'s read-only store symlinks under `root/test` with writable
    # copies because tests create files and setuid helpers beside their executables.
    rm -rf root/test
    mkdir root/test
    cp -rL ${regression.package}/. root/test/
    chmod -R u+w root/test

    cp ${./linux-regression-init.sh} root/linux-init
    chmod 0755 root/linux-init

    python3 ${./apply-linux-regression-exclusions.py} \
      "$NIX_BUILD_TOP/root/test" \
      ${../linux-regression-exclusions.tsv}

    chmod 0755 root root/test root/etc root/root root/var
    chmod 1777 root/tmp
    find root -exec touch -h -d '@1' {} +
    (cd root && find . -print0 | sort -z | cpio --quiet --null -o -H newc \
      --owner=0:0 --reproducible | gzip -n > $out/initramfs.cpio.gz)
    ln -s ${kernel} $out/bzImage
    ln -s ${hostTools} $out/host-tools
  ''
