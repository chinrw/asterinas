# SPDX-License-Identifier: MPL-2.0

import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess

from common import CACHE, CACHE_KEY, HostMonitor, capture, record_machine, roots, run, write_json

parser = argparse.ArgumentParser()
parser.add_argument("kind", choices=("timing", "profile"))
parser.add_argument("--published", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--prepare-only", action="store_true")
args = parser.parse_args()
source = Path.cwd()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
published = args.published.resolve()
rows = json.loads((published / "expected-roots.json").read_text())
context = output / "context"
(context / "test").mkdir(parents=True, exist_ok=True)
(context / "distro").mkdir(exist_ok=True)
shutil.copytree(source / "test/initramfs", context / "test/initramfs", dirs_exist_ok=True)
shutil.copy2(source / "distro/nixpkgs.nix", context / "distro/nixpkgs.nix")
shutil.copy2(source / ".dockerignore", context / ".dockerignore")
shutil.copy2(Path(__file__).with_name("measure.py"), context / "measure.py")
shutil.copy2(published / "expected-roots.json", context / "expected-roots.json")

dockerfile = (source / "tools/dev_env/docker/prebuilt-nix-packages/Dockerfile").read_text()
bootstrap, consumer = dockerfile.split("#= Build Nix packages", 1)
start = bootstrap.index("    && printf '%s\\n'")
end = bootstrap.index("    && rm /nix/var/nix/gcroots", start)
bootstrap = bootstrap[:start] + bootstrap[end:]
bootstrap = bootstrap.replace("AS build-base", "AS bootstrap")
bootstrap = bootstrap.replace("https://nixos.org/nix/install", "https://releases.nixos.org/nix/nix-2.35.3/install")
consumer = consumer[consumer.index("COPY test/initramfs"):]
nix_path_env = 'ENV PATH="/nix/var/nix/profiles/default/bin:${PATH}"'
assert consumer.count(nix_path_env) == 1
consumer = consumer.replace(nix_path_env + "\n", "")
if args.kind == "profile":
    consumer = consumer.replace("VERBOSE=0", "VERBOSE=1")
    consumer = consumer.replace("&& make nix_gc", "&& python3 /tmp/cache-measure.py --snapshot && make nix_gc")
release = "aster-nixos-release.cachix.org-1:xB6U/f5ck5vGDJZ04kPp3zGpZ4Nro9X4+TSSMAETVFE="
header = f"""
FROM bootstrap AS measured
{nix_path_env}
SHELL ["/bin/bash", "-e", "-o", "pipefail", "-c"]
ARG CACHE_MODE
ARG PROFILE_STORE=0
ENV PROFILE_STORE=${{PROFILE_STORE}}
COPY measure.py /tmp/cache-measure.py
COPY expected-roots.json /tmp/expected-roots.json
RUN python3 -c 'import json,pathlib; rows=json.load(open("/tmp/expected-roots.json")); present=sorted({{p for r in rows for p in r["paths"] if pathlib.Path(p).exists()}}); print("INITIAL_CONSUMER_PATHS "+json.dumps(present)); assert not present, present'
RUN nix --extra-experimental-features nix-command path-info --all > /tmp/initial-store.txt \\
    && python3 -c 'import pathlib; p=pathlib.Path("/tmp/initial-store.txt").read_text().splitlines(); assert p; print("INITIAL_STORE_PATH_COUNT "+str(len(p)))'
RUN printf '%s\\n' 'sandbox = false' 'max-jobs = 2' 'cores = 4' 'connect-timeout = 30' 'stalled-download-timeout = 120' 'require-sigs = true' >> /etc/nix/nix.conf \\
    && if [[ "$CACHE_MODE" == candidate ]]; then \\
        printf '%s\\n' 'extra-substituters = https://aster-nixos-release.cachix.org https://{CACHE}.cachix.org' 'extra-trusted-public-keys = {release} {CACHE_KEY}' >> /etc/nix/nix.conf; \\
       else test "$CACHE_MODE" == baseline; fi \\
    && nix --extra-experimental-features nix-command config show --json | python3 -c 'import json,os,sys; c=json.load(sys.stdin); wanted={{"https://cache.nixos.org"}}; wanted.update(["https://aster-nixos-release.cachix.org", "https://{CACHE}.cachix.org"] if os.environ["CACHE_MODE"]=="candidate" else []); assert {{x.rstrip("/") for x in c["substituters"]["value"]}}==wanted, c["substituters"]; assert c["require-sigs"]["value"]; print("CACHE_CONFIG "+json.dumps({{k:c[k]["value"] for k in ["substituters","require-sigs","max-jobs","cores","sandbox"]}}))'
SHELL ["/usr/bin/python3", "/tmp/cache-measure.py", "/bin/bash", "-c"]
"""
(context / "Dockerfile").write_text(bootstrap + header + consumer)
if args.prepare_only:
    print(context / "Dockerfile")
    raise SystemExit(0)

record_machine(output)
builder = "cachix-full-validation"
run(["docker", "buildx", "create", "--name", builder, "--driver", "docker-container", "--driver-opt", "env.BUILDKIT_STEP_LOG_MAX_SIZE=1073741824", "--driver-opt", "env.BUILDKIT_STEP_LOG_MAX_SPEED=104857600", "--buildkitd-flags", "--allow-insecure-entitlement network.host", "--use", "--bootstrap"], output / "builder.log")
version = (source / "DOCKER_IMAGE_VERSION").read_text().strip()
base = ["docker", "buildx", "build", "--builder", builder, "--allow", "network.host", "--network", "host", "--progress", "plain", "--load", "--build-arg", f"BASE_VERSION={version}"]
summary = {"kind": args.kind, "architecture": platform.machine(), "runs": [], "complete": False}
smoke_image = None
try:
    with HostMonitor(output / "bootstrap-host.jsonl"):
        summary["bootstrap"] = run([*base, "--target", "bootstrap", "-t", "cachix-common-bootstrap:validation", str(context)], output / "bootstrap.log")
    order = ("baseline", "candidate", "candidate", "baseline") if args.kind == "timing" else ("baseline", "candidate")
    for index, mode in enumerate(order):
        case = output / f"{index}-{mode}"
        case.mkdir()
        tag = f"cachix-full-validation:{index}-{mode}"
        command = [*base, "--target", "measured", "--no-cache-filter", "measured", "--build-arg", f"CACHE_MODE={mode}", "--build-arg", f"PROFILE_STORE={int(args.kind == 'profile')}", "-t", tag, str(context)]
        with HostMonitor(case / "host.jsonl"):
            result = run(command, case / "build.log")
        text = (case / "build.log").read_text()
        assert "INITIAL_CONSUMER_PATHS []" in text
        assert "output clipped" not in text, "The build log was clipped"
        result.update(mode=mode, index=index, stages=[json.loads(line.split("MEASUREMENT ", 1)[1]) for line in text.splitlines() if "MEASUREMENT {" in line], pre_gc_snapshots=[json.loads(line.split("STORE_SNAPSHOT ", 1)[1]) for line in text.splitlines() if "STORE_SNAPSHOT {" in line])
        assert len(result["pre_gc_snapshots"]) == (4 if args.kind == "profile" else 0)
        image = json.loads(capture(["docker", "image", "inspect", tag]))[0]
        result.update(image_id=image["Id"], image_size_bytes=image["Size"])
        write_json(case / "image.json", image)
        summary["runs"].append(result)
        write_json(output / "summary.json", summary)
        verify = case / "verify"
        verify.mkdir()
        harness = Path(__file__).parent.resolve()
        run(["docker", "run", "--rm", "--network", "host", "-v", f"{harness}:/harness:ro", "-v", f"{published / 'expected-roots.json'}:/expected-roots.json:ro", "-v", f"{verify}:/evidence", tag, "python3", "/harness/verify.py"], case / "verification.log")
        closure = json.loads((verify / "runtime-closure.json").read_text())
        producer = json.loads((published / "published-closure.json").read_text())
        result["root_hash_comparison"] = {path: {"producer": producer[path]["narHash"], "consumer": closure[path]["narHash"], "equal": producer[path]["narHash"] == closure[path]["narHash"]} for path in roots(rows)}
        initramfs_info = json.loads((verify / "initramfs.json").read_text())
        initramfs = initramfs_info["path"]
        store_path_hash = Path(initramfs).name.split("-", 1)[0]
        statuses = {}
        for endpoint in ("cache.nixos.org", "aster-nixos-release.cachix.org", f"{CACHE}.cachix.org"):
            statuses[endpoint] = capture(["curl", "--silent", "--show-error", "--connect-timeout", "10", "--max-time", "60", "--output", str(case / f"initramfs-{endpoint}.narinfo"), "--write-out", "%{http_code}", f"https://{endpoint}/{store_path_hash}.narinfo"])
        result["initramfs_cache_http"] = statuses
        if mode == "candidate":
            assert set(statuses.values()) == {"404"}, ("The miss-fallback output was unexpectedly cached", statuses)
            assert not initramfs_info["initially_present"]
            assert initramfs_info["metadata"]["ultimate"], "The initramfs output was not built locally"
            if args.kind == "profile":
                assert f"building '{initramfs_info['deriver']}'" in text, "The fallback derivation was not built"
            result["root_signers"] = {path: [sig.split(":", 1)[0] for sig in closure[path]["signatures"]] for path in roots(rows)}
            allowed = {"cache.nixos.org-1", "aster-nixos-release.cachix.org-1", f"{CACHE}.cachix.org-1"}
            assert all(allowed.intersection(signers) for signers in result["root_signers"].values()), "A required root was not downloaded from an allowed cache"
            assert any(f"{CACHE}.cachix.org-1" in signers for signers in result["root_signers"].values()), "No required root came from the personal cache"
        write_json(output / "summary.json", summary)
        print(json.dumps({key: value for key, value in result.items() if key not in ("stages", "root_hash_comparison")}), flush=True)
        if args.kind == "timing" and mode == "candidate" and smoke_image is None:
            smoke_image = tag
        else:
            subprocess.run(["docker", "image", "rm", tag], check=True, stdout=subprocess.DEVNULL)
    assert all(row["equal"] for result in summary["runs"] for row in result["root_hash_comparison"].values()), "Baseline and cached output bytes differ"
    if smoke_image:
        kernel_dockerfile = (source / "tools/dev_env/docker/kernel-dev/Dockerfile").read_text()
        vdso = kernel_dockerfile.split("#= Download VDSO files", 1)[1].split("#= Build syscall test", 1)[0]
        smoke_file = output / "smoke.Dockerfile"
        smoke_file.write_text(f"FROM {smoke_image}\nSHELL [\"/bin/bash\", \"-c\"]\n#= Download VDSO files" + vdso + "\nRUN apt-get update && apt-get install -y --no-install-recommends exfatprogs e2fsprogs cpio && rm -rf /var/lib/apt/lists/*\n")
        run(["docker", "buildx", "build", "--builder", "default", "--load", "--network", "host", "--progress", "plain", "-f", str(smoke_file), "-t", "cachix-kernel-smoke:validation", str(context)], output / "smoke-image.log")
        smoke = ["docker", "run", "--rm", "--privileged", "--network", "host", "-v", "/dev:/dev", "-v", f"{source}:/root/asterinas", "-w", "/root/asterinas", "cachix-kernel-smoke:validation", "bash", "-c"]
        run([*smoke, "git config --global --add safe.directory /root/asterinas && make kernel TARGET_ARCH=riscv64 && make kernel TARGET_ARCH=x86_64 && timeout 1200 make run_kernel TARGET_ARCH=x86_64 AUTO_TEST=boot ENABLE_KVM=0"], output / "kernel-smoke.log")
        summary["kernel_smoke"] = "passed"
    summary["complete"] = True
finally:
    write_json(output / "summary.json", summary)
    subprocess.run(["docker", "buildx", "rm", builder], stdout=subprocess.DEVNULL)
