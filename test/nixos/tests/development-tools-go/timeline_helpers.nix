# SPDX-License-Identifier: MPL-2.0
{ pkgs }:
let
  make =
    sleepUs:
    pkgs.runCommand "notingo-timeline-${toString sleepUs}us"
      {
        nativeBuildInputs = [
          pkgs.go_1_26
          pkgs.gcc
          pkgs.python3
        ];
        GO111MODULE = "off";
        GOEXPERIMENT = "goroutineleakprofile";
      }
      ''
         export GOCACHE="$TMPDIR/go-cache" GOPATH="$TMPDIR/go-path" GOENV=off
         cp ${pkgs.go_1_26}/share/go/src/runtime/proc.go "$TMPDIR/proc.go"
         chmod u+w "$TMPDIR/proc.go"
         python3 - "$TMPDIR/proc.go" ${toString sleepUs} <<'PYCODE'
        import sys
        from pathlib import Path
        p=Path(sys.argv[1]);s=p.read_text();needle="func sysmon() {"
        assert s.count(needle)==1
        s=s.replace(needle,needle+'\n\tprintln("TIMELINE_SYSMON_TID", gettid())')
        assert s.count("usleep_no_g(1)")==1
        s=s.replace("usleep_no_g(1)","usleep_no_g("+sys.argv[2]+")")
        p.write_text(s)
        PYCODE
         cat > "$TMPDIR/overlay.json" <<JSON
        {"Replace":{"${pkgs.go_1_26}/share/go/src/runtime/proc.go":"$TMPDIR/proc.go"}}
        JSON
         mkdir -p "$out/bin" "$out/share/licenses/go"
         go build -overlay "$TMPDIR/overlay.json" -o "$out/bin/minimal-cgo" ${./minimal_cgo.go}
         cp ${./GO-LICENSE} "$out/share/licenses/go/LICENSE"
      '';
in
{
  original = make 1;
  sleep20 = make 20;
  tools = pkgs.runCommand "runtime-timeline-tools" { nativeBuildInputs = [ pkgs.gcc ]; } ''
    mkdir -p "$out/bin"
    gcc -O2 -Wall -Wextra ${./timeline_launcher.c} -o "$out/bin/launcher"
    gcc -O2 -Wall -Wextra ${./timeline_upload.c} -o "$out/bin/upload"
    gcc -O2 -Wall -Wextra ${./linux_nanosleep_switches.c} -pthread -o "$out/bin/linux-sleep-control"
  '';
}
