# SPDX-License-Identifier: MPL-2.0

from pathlib import Path
import subprocess

from common import run

output = Path("preflight").resolve()
output.mkdir(exist_ok=True)
(output / "Dockerfile").write_text("""FROM busybox:1.37 AS bootstrap
RUN echo BOOTSTRAP_READY
FROM bootstrap AS measured
RUN awk 'BEGIN { for (i=0; i<80000; i++) print "abcdefghijabcdefghijabcdefghijabcdefghijabcdefghijabcdefghij" }'; echo LOG_COMPLETE
""")
builder = "cachix-preflight"
run(["docker", "buildx", "create", "--name", builder, "--driver", "docker-container", "--driver-opt", "env.BUILDKIT_STEP_LOG_MAX_SIZE=1073741824", "--driver-opt", "env.BUILDKIT_STEP_LOG_MAX_SPEED=104857600", "--buildkitd-flags", "--allow-insecure-entitlement network.host", "--bootstrap"], output / "builder.log")
base = ["docker", "buildx", "build", "--builder", builder, "--allow", "network.host", "--network", "host", "--progress", "plain", "--load", "-t", "cachix-preflight:probe"]
try:
    for name, options in (("bootstrap", ["--target", "bootstrap"]), ("first", ["--target", "measured", "--no-cache-filter", "measured"]), ("second", ["--target", "measured", "--no-cache-filter", "measured"])):
        run([*base, *options, str(output)], output / f"{name}.log")
        text = (output / f"{name}.log").read_text()
        if name != "bootstrap":
            assert "LOG_COMPLETE" in text and "output clipped" not in text
            assert text.count("abcdefghijabcdefghijabcdefghijabcdefghijabcdefghijabcdefghij") >= 80000
            assert "CACHED" in text
        print(name, len(text), "passed", flush=True)
finally:
    subprocess.run(["docker", "buildx", "rm", builder], stdout=subprocess.DEVNULL)
