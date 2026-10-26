# SPDX-License-Identifier: MPL-2.0

{ pkgs }:

pkgs.runCommand "notingo-shared-helpers"
  {
    nativeBuildInputs = [
      pkgs.go_1_26
      pkgs.gcc
    ];
    GO111MODULE = "off";
    GOEXPERIMENT = "goroutineleakprofile";
  }
  ''
    export GOCACHE="$TMPDIR/go-cache"
    export GOPATH="$TMPDIR/go-path"
    export GOENV=off
    mkdir -p "$out/bin" "$out/share/licenses/go"
    cd ${pkgs.go_1_26}/share/go/src/runtime/testdata/testprogcgo
    go build -o "$out/bin/original" .
    go build -o "$out/bin/clock-probe" ${./clock_probe.go}
    cp ${./GO-LICENSE} "$out/share/licenses/go/LICENSE"
  ''
