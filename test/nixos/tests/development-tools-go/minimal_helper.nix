# SPDX-License-Identifier: MPL-2.0
{ pkgs }:
pkgs.runCommand "notingo-minimal-cgo"
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
    mkdir -p "$out/bin"
    go build -o "$out/bin/minimal-cgo" ${./minimal_cgo.go}
  ''
