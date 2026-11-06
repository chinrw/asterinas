# SPDX-License-Identifier: MPL-2.0
{
  pkgs,
  root ? ../../..,
}:
let
  tests = pkgs.callPackage (root + "/test/initramfs/nix/regression/common.nix") {
    dir = "hello_world";
  };
  regression = {
    package = pkgs.runCommand "c2n-existing-hello-world" { } ''
      mkdir -p "$out"
      ln -s ${tests}/hello_world "$out/hello_world"
      cat > "$out/run_c2n_hello.sh" <<'EOF'
      #!/bin/sh
      set -e
      cd /test/hello_world
      ./run_test.sh
      echo C2N_HELLO_WORLD_SUCCEEDED
      EOF
      chmod +x "$out/run_c2n_hello.sh"
    '';
  };
  initramfs = pkgs.callPackage (root + "/test/initramfs/nix/initramfs.nix") {
    inherit regression;
    busybox = pkgs.busybox;
    benchmark = null;
    conformance = null;
    dnsServer = "127.0.0.1";
  };
in
pkgs.callPackage (root + "/test/initramfs/nix/initramfs-image.nix") {
  inherit initramfs;
  compressed = true;
}
