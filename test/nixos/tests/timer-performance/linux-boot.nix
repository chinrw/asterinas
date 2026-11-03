# SPDX-License-Identifier: MPL-2.0
{ source ? ../../../.. }:
let
  pkgs = import (source + "/distro/nixpkgs.nix") { };
  kernel = pkgs.linuxPackages.kernel;
  rootModules = [
    "virtio_pci" "virtio_blk" "virtio_net" "virtio_console" "virtio_rng"
    "ext2" "vfat" "nls_cp437" "nls_iso8859_1" "autofs4"
  ];
  firmware = pkgs.runCommand "linux-control-empty-firmware" { } "mkdir -p $out/lib/firmware";
  modules = pkgs.makeModulesClosure {
    kernel = kernel.modules;
    inherit rootModules firmware;
  };
  init = pkgs.writeShellScript "linux-control-stage1" ''
    export PATH=${pkgs.busybox}/bin
    mkdir -p /dev /proc /sys
    mount -t devtmpfs devtmpfs /dev
    mount -t proc proc /proc
    mount -t sysfs sysfs /sys
    mkdir -p /dev/pts
    mount -t devpts devpts /dev/pts
    ln -sfn pts/ptmx /dev/ptmx
    ln -sfn /proc/self/fd /dev/fd
    ln -sfn /proc/self/fd/0 /dev/stdin
    ln -sfn /proc/self/fd/1 /dev/stdout
    ln -sfn /proc/self/fd/2 /dev/stderr
    for module in ${builtins.concatStringsSep " " rootModules}; do
      ${pkgs.kmod}/bin/modprobe --config /dev/null --dirname ${modules} --set-version ${kernel.modDirVersion} "$module" || exit 1
    done
    for attempt in $(seq 1 100); do
      [ -b /dev/vda2 ] && break
      sleep 0.1
    done
    ip link set lo up
    # The unchanged Asterinas userland disables network-setup and udev.
    # Linux therefore needs its existing virtual NIC configured before stage 2.
    for interface in /sys/class/net/*; do
      interface=''${interface##*/}
      [ "$interface" = lo ] && continue
      ip link set "$interface" up
      ip addr add 10.0.2.15/24 dev "$interface"
      ip route add default via 10.0.2.2 dev "$interface"
    done
    mkdir -p /sysroot
    mount -t ext2 /dev/vda2 /sysroot || exit 1
    mkdir -p /sysroot/proc /sysroot/sys /sysroot/dev
    mount --move /proc /sysroot/proc
    mount --move /sys /sysroot/sys
    mount --move /dev /sysroot/dev
    echo LINUX_CONTROL_STAGE1_SWITCH_ROOT
    exec switch_root /sysroot /nix/var/nix/profiles/system/init
  '';
  initrd = pkgs.makeInitrd {
    name = "linux-control-initrd";
    contents = [
      { object = "${pkgs.busybox}/bin"; symlink = "/bin"; }
      { object = init; symlink = "/init"; }
      { object = modules; symlink = "/modules"; }
      { object = pkgs.kmod; symlink = "/kmod"; }
    ];
  };
in
pkgs.runCommand "linux-control-boot" { } ''
  mkdir -p $out
  ln -s ${kernel}/bzImage $out/kernel
  ln -s ${kernel}/System.map $out/System.map
  ln -s ${initrd}/initrd $out/initrd
  ln -s ${modules} $out/modules
  printf '%s\n' '${kernel.version}' > $out/kernel-version
  printf '%s\n' '${toString pkgs.path}' > $out/nixpkgs-path
''
