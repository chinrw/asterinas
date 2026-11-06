#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0

"""Record compiler invocations without capturing credentials from the environment."""

import json
import os
import subprocess
import sys
import time

started = time.monotonic()
result = subprocess.run(sys.argv[1:], check=False)
record = {
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "env": {
        key: value
        for key, value in os.environ.items()
        if key.startswith(("CARGO_PKG_", "OSDK_BUILD_"))
        or key in ("OSTD_TASK_STACK_SIZE_IN_PAGES", "ASTER_BUILD_TIMESTAMP", "RUSTFLAGS")
    },
    "exit_code": result.returncode,
    "elapsed_seconds": time.monotonic() - started,
}
with open(os.environ["C2N_RUSTC_LOG"], "a") as output:
    output.write(json.dumps(record) + "\n")
sys.exit(result.returncode)
