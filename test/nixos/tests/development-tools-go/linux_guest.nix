# SPDX-License-Identifier: MPL-2.0

{ pkgs }:
let
  kernel = pkgs.linuxPackages.kernel;
  modules = pkgs.makeModulesClosure {
    kernel = kernel.modules;
    firmware = pkgs.runCommand "notingo-empty-firmware" { } ''
      mkdir -p "$out/lib/firmware"
    '';
    rootModules = [
      "virtio_pci"
      "virtio_blk"
      "virtio_console"
      "virtio_rng"
      "ext4"
    ];
    allowMissing = false;
  };
  init = pkgs.writeShellScript "notingo-linux-init" ''
    set -e
    export PATH=${pkgs.busybox}/bin
    mkdir -p /dev
    mount -t devtmpfs devtmpfs /dev
    ${pkgs.kmod}/bin/modprobe -d ${modules} -S ${kernel.modDirVersion} virtio_pci
    ${pkgs.kmod}/bin/modprobe -d ${modules} -S ${kernel.modDirVersion} virtio_console
    # The tty node can appear before virtio-console adds its port to the console list.
    # Wait until it can be opened before attaching init to it.
    for attempt in $(seq 1 100); do
      if ( : < /dev/hvc0 ) 2>/dev/null; then break; fi
      sleep 0.1
    done
    exec < /dev/hvc0 > /dev/hvc0 2>&1
    echo LINUX_GUEST_INIT
    ${pkgs.kmod}/bin/modprobe -d ${modules} -S ${kernel.modDirVersion} virtio_rng
    ${pkgs.kmod}/bin/modprobe -d ${modules} -S ${kernel.modDirVersion} virtio_blk
    ${pkgs.kmod}/bin/modprobe -d ${modules} -S ${kernel.modDirVersion} ext4
    for attempt in $(seq 1 100); do
      if [ -e /dev/vda2 ]; then break; fi
      sleep 0.1
    done
    mkdir -p /dev/pts /sysroot
    mount -t devpts devpts /dev/pts
    chmod 0666 /dev/pts/ptmx
    ln -sfn pts/ptmx /dev/ptmx
    mount -t ext4 /dev/vda2 /sysroot
    mount -t proc none /sysroot/proc
    mount --move /dev /sysroot/dev
    exec switch_root /sysroot /nix/var/nix/profiles/system/init
  '';
in
{
  kernel = "${kernel}/bzImage";
  version = kernel.modDirVersion;
  initrd = pkgs.makeInitrd {
    contents = [
      {
        object = "${pkgs.busybox}/bin";
        symlink = "/bin";
      }
      {
        object = modules;
        symlink = "/modules";
      }
      {
        object = pkgs.kmod;
        symlink = "/kmod";
      }
      {
        object = init;
        symlink = "/init";
      }
    ];
  };
}
