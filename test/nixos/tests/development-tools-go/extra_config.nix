{ pkgs, lib, ... }:

let
  linuxGuestEnabled = builtins.getEnv "PROBE_LINUX_GUEST" == "1";
  linuxGuest = import ./linux_guest.nix { inherit pkgs; };
  sharedHelpers = import ./shared_helpers.nix { inherit pkgs; };
  minimalHelper = import ./minimal_helper.nix { inherit pkgs; };
in
{
  aster_nixos.kernel = lib.mkIf linuxGuestEnabled linuxGuest.kernel;
  system.systemBuilderCommands = lib.mkIf linuxGuestEnabled (
    lib.mkAfter ''
      ln -sfn ${linuxGuest.initrd}/initrd $out/initrd
    ''
  );
  environment.systemPackages = with pkgs; [
    go_1_26
    gcc
    python3
  ];
  environment.etc = {
    "notingo/build.py".source = ./build_notingo_probe.py;
    "notingo/wait.c".source = ./wait_primitive_probe.c;
    "notingo/signal.c".source = ./futex_signal_probe.c;
    "notingo/allocation.c".source = ./short_sleep_allocation.c;
    "notingo/minimal".source = "${minimalHelper}/bin/minimal-cgo";
    "notingo/shared".source = "${sharedHelpers}/bin/original";
    "notingo/clock-probe".source = "${sharedHelpers}/bin/clock-probe";
  }
  // lib.optionalAttrs linuxGuestEnabled {
    "notingo/linux-version".text = linuxGuest.version;
  };
}
