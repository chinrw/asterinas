{
  disable-systemd ? "false",
  stage-2-hook ? "/bin/sh -l",
  log-level ? "error",
  console ? "hvc0",
  extra-substituters ? toString (import ../../flake.nix).nixConfig.extra-substituters,
  extra-trusted-public-keys ? toString (import ../../flake.nix).nixConfig.extra-trusted-public-keys,
  config-file-name ? "configuration.nix",
  target_platform ? "x86_64-linux",
  kernel ? ../../target/osdk/iso_root/boot/asterinas-osdk-bin,
  pkgs ? import ../../tools/nix/pkgs.nix { },
}:
let
  asterinas = builtins.path {
    name = "asterinas-osdk-bin";
    path = kernel;
  };
  etc-nixos = builtins.path { path = ../etc_nixos; };

  aster_defaults = import ./defaults.nix {
    inherit (pkgs) lib;
    inherit
      disable-systemd
      stage-2-hook
      log-level
      console
      target_platform
      ;
    kernel = asterinas;
    substituters = extra-substituters;
    trusted-public-keys = extra-trusted-public-keys;
  };
  aster_nixos_install = pkgs.replaceVarsWith {
    src = ./templates/aster-nixos-install;
    replacements = {
      aster-defaults = aster_defaults;
      aster-etc-nixos = etc-nixos;
      aster-nixpkgs = builtins.storePath pkgs.path;
      aster-target-platform = target_platform;
      aster-substituters = extra-substituters;
      aster-trusted-public-keys = extra-trusted-public-keys;
    };
    isExecutable = true;
  };

in
pkgs.stdenv.mkDerivation {
  name = "aster_nixos_installer";
  buildCommand = ''
    mkdir -p $out/{bin,etc_nixos}
    install -m 755 ${aster_nixos_install} $out/bin/aster-nixos-install
    cp -L ${etc-nixos}/aster_configuration.nix $out/etc_nixos/aster_configuration.nix
    cp -L ${aster_defaults} $out/etc_nixos/aster_defaults.nix
    cp -L ${etc-nixos}/${config-file-name} $out/etc_nixos/configuration.nix
    cp -r ${etc-nixos}/modules $out/etc_nixos/modules
    cp -r ${etc-nixos}/overlays $out/etc_nixos/overlays
    ln -s ${asterinas} $out/kernel
  '';

  passthru.defaults = aster_defaults;
}
