{ pkgs, ... }:

{
  environment.systemPackages = with pkgs; [
    go_1_26
    gcc
    python3
  ];
  environment.etc."notingo/build.py".source = ./build_notingo_probe.py;
}
