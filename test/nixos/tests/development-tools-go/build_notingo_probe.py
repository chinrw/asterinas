# SPDX-License-Identifier: MPL-2.0

import os
from pathlib import Path
import json
import sys
import subprocess

source_root = Path(subprocess.check_output(["go", "env", "GOROOT"], text=True).strip())
source = source_root / "src/runtime/testdata/testprogcgo"
work = Path("/tmp/notingo-probe-src")
work.mkdir(exist_ok=True)
text = (source / "notingo.go").read_text()
include = "#include <pthread.h>"
assert text.count(include) == 1
text = text.replace(include, include + "\n#include <stdio.h>\n#include <stdlib.h>", 1)
for function in ("enterGoThenSpinTwice", "enterGoThenWait"):
    original = f"pthread_create(&tid, NULL, {function}, NULL);"
    replacement = (
        f"int result = pthread_create(&tid, NULL, {function}, NULL);\n"
        '\tif (result != 0) {\n'
        '\t\tfprintf(stderr, "PTHREAD_CREATE_FAILED result=%d\\n", result);\n'
        '\t\texit(77);\n'
        '\t}'
    )
    assert text.count(original) == 1
    text = text.replace(original, replacement, 1)
backing = work / "notingo.go"
backing.write_text(text)
overlay = work / "overlay.json"
overlay.write_text(json.dumps({"Replace": {str(source / "notingo.go"): str(backing)}}))
if "--prepare-only" in sys.argv:
    print("PROBE_OVERLAY_READY")
else:
    experiment = os.environ.get("GOEXPERIMENT", "")
    experiment = experiment + ",goroutineleakprofile" if experiment else "goroutineleakprofile"
    subprocess.run(
        ["go", "build", "-overlay=" + str(overlay), "-o", "/tmp/notingo-probe", "."],
        cwd=source,
        env=dict(os.environ, GO111MODULE="off", GOEXPERIMENT=experiment),
        check=True,
    )
    assert b"PTHREAD_CREATE_FAILED" in Path("/tmp/notingo-probe").read_bytes()
    print("PROBE_BUILD_OK")
