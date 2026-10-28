{ pkgs, ... }:
let
  minimalHelper = import ./minimal_helper.nix { inherit pkgs; };
  foreignWaitHelpers = import ./foreign_wait_helpers.nix { inherit pkgs; };
in
{
  environment.systemPackages = with pkgs; [
    go_1_26
    gcc
    python3
  ];
  environment.etc = {
    "notingo/minimal".source = "${minimalHelper}/bin/minimal-cgo";
    "notingo/sleep20".source = "${foreignWaitHelpers.sleep20}/bin/minimal-cgo";
    "notingo/sleep1000".source = "${foreignWaitHelpers.sleep1000}/bin/minimal-cgo";
  };
}
