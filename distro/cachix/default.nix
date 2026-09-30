{
  pkgs ? import ../../tools/nix/pkgs.nix { },
  ...
}:
let
  installer = pkgs.callPackage ../aster_nixos_installer { };
  # Evaluate the configuration from the repository. Only the installer's
  # defaults file is taken, and that one exists without building the installer.
  nixos = pkgs.nixos {
    imports = [
      ../etc_nixos/configuration.nix
      installer.defaults
    ];
  };
  cachixPkgs =
    with nixos.pkgs;
    [
      hello-asterinas
      xfce.xfdesktop
      xfce.xfwm4
      xorg.xorgserver
      runc
      runc.man
      podman
      podman.man
      aster_systemd
      jtreg
    ]
    ++ (with nixos.config; [
      system.build.toplevel
      systemd.package
      systemd.package.debug
      systemd.package.dev
      systemd.package.man
      virtualisation.podman.package
      virtualisation.podman.package.man
    ]);
in
pkgs.writeClosure cachixPkgs
