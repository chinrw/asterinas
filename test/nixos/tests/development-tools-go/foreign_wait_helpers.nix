# SPDX-License-Identifier: MPL-2.0
{ pkgs }:
let
  make =
    sleepUs:
    pkgs.runCommand "notingo-sleep-${toString sleepUs}us"
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
            original=${pkgs.go_1_26}/share/go/src/runtime/proc.go
            test "$(grep -Fc 'usleep_no_g(1)' "$original")" -eq 1
            cp "$original" "$TMPDIR/proc.go"
            chmod u+w "$TMPDIR/proc.go"
            substituteInPlace "$TMPDIR/proc.go" --replace-fail 'usleep_no_g(1)' 'usleep_no_g(${toString sleepUs})'
            cat > "$TMPDIR/overlay.json" <<JSON
            {"Replace":{"$original":"$TMPDIR/proc.go"}}
        JSON
            mkdir -p "$out/bin" "$out/share/licenses/go"
            go build -overlay "$TMPDIR/overlay.json" -o "$out/bin/minimal-cgo" ${./minimal_cgo.go}
            cp ${./GO-LICENSE} "$out/share/licenses/go/LICENSE"
            printf '%s\n' '${toString sleepUs}' > "$out/sleep-microseconds"
            sha256sum "$original" "$TMPDIR/proc.go" > "$out/runtime-source-sha256.txt"
      '';
in
{
  sleep20 = make 20;
  sleep1000 = make 1000;
}
