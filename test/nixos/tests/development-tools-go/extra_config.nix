{ pkgs, ... }:

let
  sharedHelpers = import ./shared_helpers.nix { inherit pkgs; };
in
{
  environment.systemPackages = with pkgs; [
    go_1_26
    gcc
    python3
  ];
  environment.etc."notingo/build.py".source = ./build_notingo_probe.py;
  environment.etc."notingo/shared".source = "${sharedHelpers}/bin/original";
  environment.etc."notingo/clock-probe".source = "${sharedHelpers}/bin/clock-probe";
}
