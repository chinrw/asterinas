# Renders /etc/nixos/aster_defaults.nix, the settings chosen at installation time.
# builtins.toFile writes the file during evaluation, so distro/cachix can import it
# without building the installer first.
{
  lib,
  kernel,
  disable-systemd,
  stage-2-hook,
  log-level,
  console,
  target_platform,
  substituters,
  trusted-public-keys,
}:
let
  str = lib.strings.escapeNixString;
in
builtins.toFile "aster_defaults.nix" ''
  # The settings chosen when this system was installed.
  { lib, ... }:
  {
    nixpkgs.hostPlatform = lib.mkDefault ${str target_platform};

    aster_nixos = {
      kernel = lib.mkOptionDefault ${str "${kernel}"};
      disable-systemd = lib.mkOptionDefault ${str disable-systemd};
      stage-2-hook = lib.mkOptionDefault ${str stage-2-hook};
      log-level = lib.mkOptionDefault ${str log-level};
      console = lib.mkOptionDefault ${str console};
      substituters = lib.mkOptionDefault ${str substituters};
      trusted-public-keys = lib.mkOptionDefault ${str trusted-public-keys};
    };
  }
''
