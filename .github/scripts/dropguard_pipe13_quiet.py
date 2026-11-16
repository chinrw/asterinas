#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Repeat the original quiet pipe13 group on one host without changing guest inputs."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
CONTRACT = json.loads((HERE / "dropguard_pipe13_quiet_contract.json").read_text())
EXPECTED = "".join(row + "\n" for row in CONTRACT["original_selected_rows"]).encode()
assert hashlib.sha256(EXPECTED).hexdigest() == CONTRACT["runtest"]["selected_bytes_sha256"]
WORKSPACE = "/__w/asterinas/asterinas"
OUTPUT = Path(os.environ.get("QUIET_OUTPUT", "/tmp/dropguard-pipe13-quiet-output"))
CHECKOUTS = Path(os.environ.get("GITHUB_WORKSPACE", ".")) / "checkouts"
STATE = OUTPUT / "state.json"
DISKS = CONTRACT["guest"]["attached_images"]
PARAMETERS = "TARGET_ARCH=riscv64 AUTO_TEST=conformance CONFORMANCE_TEST_SUITE=ltp SMP=4 RELEASE=0"


class ContractMismatch(RuntimeError):
    """An observed input or build setting differs from the recorded quiet contract."""


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def load():
    return json.loads(STATE.read_text())


def command(arguments, output=None, check=True, timeout=None, inherit=False):
    if inherit:
        result = subprocess.run(arguments, timeout=timeout)
        text = ""
    elif output is None:
        result = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        text = result.stdout.decode(errors="replace")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("wb") as stream:
            result = subprocess.run(arguments, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
        text = ""
    if check and result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {arguments!r}\n{text[-4000:]}")
    return result.returncode, text


def container_name(version):
    return f"dropguard-quiet-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}-{version}"


def docker(version, script, output=None, check=True, timeout=None, env=None, inherit=False):
    arguments = ["docker", "exec"]
    for key, value in (env or {}).items():
        arguments += ["-e", f"{key}={value}"]
    return command(arguments + ["--workdir", WORKSPACE, container_name(version),
                    "bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                   output=output, check=check, timeout=timeout, inherit=inherit)


def container_read(version, path):
    return subprocess.check_output(["docker", "exec", "--workdir", WORKSPACE, container_name(version), "cat", path])


def media_metadata(version):
    script = "python3 - <<'PY'\npaths=" + repr(DISKS) + "\n" + r'''
import hashlib,json,pathlib
rows=[]
for name in paths:
 path=pathlib.Path(name);digest=hashlib.sha256()
 with path.open('rb') as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
 rows.append({'file':name,'sha256':digest.hexdigest(),'bytes':path.stat().st_size,'mtime_ns':path.stat().st_mtime_ns})
print(json.dumps(rows))
PY'''
    return json.loads(docker(version, script)[1])


def nix_metadata(version, stage):
    script = "python3 - <<'PY'\nstage=" + repr(stage) + "\n" + r'''
import hashlib,json,pathlib,subprocess
contract=json.loads(pathlib.Path('/quiet-controller/dropguard_pipe13_quiet_contract.json').read_text())
warnings=[]
def sha(path):
 result=hashlib.sha256()
 with pathlib.Path(path).open('rb') as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):result.update(block)
 return result.hexdigest()
def execute(arguments):
 p=subprocess.run(arguments,capture_output=True,text=True,check=True)
 if p.stderr:warnings.append({'command':arguments,'stderr':p.stderr})
 return p.stdout.strip()
def nar(path):
 p=subprocess.Popen(['nix-store','--dump',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 value=hashlib.sha256()
 for block in iter(lambda:p.stdout.read(1024*1024),b''):value.update(block)
 _,stderr=p.communicate()
 assert p.returncode==0,stderr.decode(errors='replace')
 if stderr:warnings.append({'command':['nix-store','--dump',str(path)],'stderr':stderr.decode(errors='replace')})
 return value.hexdigest()
directory=pathlib.Path('test/initramfs/build/initramfs').resolve(strict=True)
image=pathlib.Path('test/initramfs/build/initramfs.cpio.gz').resolve(strict=True)
drv=execute(['nix-store','--query','--deriver',str(directory)])
references=execute(['nix-store','--query','--references',drv]).splitlines()
expected_drv=contract['conformance_derivations'][stage]
assert expected_drv in references,(expected_drv,references)
document=json.loads(execute(['nix','--extra-experimental-features','nix-command','derivation','show',expected_drv,drv]))
assert document['version']==4,document['version']
derivations={'/nix/store/'+name:value for name,value in document['derivations'].items()}
assert contract['original_ltp_bytes']['ltp_prebuilt_path'] in derivations[expected_drv]['env']['buildCommand']
data={'directory':str(directory),'image':str(image),'directory_nar_sha256':nar(directory),'image_sha256':sha(image),
      'image_nar_sha256':nar(image),'initramfs_drv':drv,'derivations':derivations,
      'host_resolv_conf':pathlib.Path('/etc/resolv.conf').read_text(),
      'host_resolv_sha256':sha('/etc/resolv.conf'),'guest_resolv_conf':(directory/'etc/resolv.conf').read_text()}
settings=json.loads(execute(['nix','--extra-experimental-features','nix-command','show-config','--json']))
data['nix_configuration']={key:settings[key]['value'] for key in ['min-free','max-free','auto-optimise-store','build-users-group']}
assert data['nix_configuration']['min-free']==0 and data['nix_configuration']['auto-optimise-store'] is False
assert data['guest_resolv_conf']=='nameserver 1.1.1.1\n'
sources={row['path']:row['sha256'] for row in contract['unchanged_source_input_files']}
for packaged,source in [('init','test/initramfs/src/init'),('opt/run_conformance_test.sh','test/initramfs/src/conformance/run_conformance_test.sh'),('opt/ltp/run_ltp_test.sh','test/initramfs/src/conformance/ltp/run_ltp_test.sh')]:
 assert sha(directory/packaged)==sources[source],packaged
ltp=contract['original_ltp_bytes']
assert nar(ltp['ltp_prebuilt_path'])==ltp['actual_nar_sha256']
for row in ltp['binary_cases']:
 actual=directory/'opt/ltp/testcases/bin'/row['binary']
 assert actual.stat().st_size==row['bytes'] and sha(actual)==row['sha256'],row
for name,value in ltp['script_hashes'].items():assert sha(directory/'opt/ltp'/name)==value,name
data.update(original_ltp_nar_sha256=ltp['actual_nar_sha256'],original_binary_cases_verified=len(ltp['binary_cases']))
data['nix_command_warnings']=warnings
print(json.dumps(data))
PY'''
    return json.loads(docker(version, script)[1])


def stop_residual_guest(version):
    script = r'''python3 - <<'PY'
import json,os,pathlib,signal,time
names={'qemu-system-riscv64','cargo-osdk','cargo','make'}
def processes():
 result=[]
 for path in pathlib.Path('/proc').iterdir():
  if not path.name.isdigit():continue
  try:
   executable=os.readlink(path/'exe')
   if executable.rsplit('/',1)[-1] in names:result.append({'pid':int(path.name),'executable':executable})
  except (FileNotFoundError,ProcessLookupError):pass
 return result
found=processes();events=[]
for value in found:
 try:os.kill(value['pid'],signal.SIGTERM);events.append({**value,'signal':'TERM'})
 except ProcessLookupError:pass
deadline=time.monotonic()+15
while processes() and time.monotonic()<deadline:time.sleep(.1)
for value in processes():
 try:os.kill(value['pid'],signal.SIGKILL);events.append({**value,'signal':'KILL'})
 except ProcessLookupError:pass
deadline=time.monotonic()+5
while processes() and time.monotonic()<deadline:time.sleep(.1)
remaining=processes()
print(json.dumps({'signals':events,'remaining':remaining}))
assert not remaining,remaining
PY'''
    return json.loads(docker(version, script)[1])


def collect_round(version, directory, row):
    row["residual_guest_cleanup"] = stop_residual_guest(version)
    checkout = CHECKOUTS / version
    for name in ["qemu.log", "qemu-serial.log", "selected-run.log", "selected-runtest.txt"]:
        if (checkout / name).exists():
            shutil.copy2(checkout / name, directory / name)
    row["guest_output_observed"] = (directory / "qemu.log").exists() and (directory / "qemu.log").stat().st_size > 0
    coverage = parse_quiet(directory / "qemu.log", directory / "coverage.json")
    row.update(complete_original_cases=coverage["complete_original_cases"],
               strict_verifier_exit_code=0 if coverage["all_tests_passed"] else 1,
               counts=coverage["counts"], pipe13=coverage["pipe13"],
               failed_or_broken=coverage["failed_or_broken"],
               missing=coverage["missing"], extra=coverage["extra"],
               ended_monotonic=time.monotonic())
    return coverage


def recover_interrupted(state):
    if not state.get("rounds") or state["rounds"][-1]["status"] != "started":
        return
    row = state["rounds"][-1]
    directory = OUTPUT / f"round-{row['round']:02d}-{row['version']}"
    try:
        collect_round(row["version"], directory, row)
        row.update(status="step_interrupted", make_pipeline_exit_code=None,
                   interruption="The prior step ended before its controller could record the exit code.")
    except Exception as error:
        row.update(status="cleanup_error", error=str(error))
        state.update(stopped=True, stop_reason="Could not safely collect and stop the interrupted guest.")
    save(directory / "round.json", row)
    save(STATE, state)


def clean_text(raw):
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw).replace("\r", "")


def parse_quiet(log, output):
    text = clean_text(log.read_text(errors="replace")) if log.exists() else ""
    rows = re.findall(r"^([A-Za-z0-9_]+): (pass|skip|fail|broken)\s+\(([^\n]+)\)$", text, re.MULTILINE)
    expected = [row.split()[0] for row in EXPECTED.decode().splitlines()]
    names = [name for name, _, _ in rows]
    results = [{"name": name, "status": status, "duration": duration} for name, status, duration in rows]
    coverage = {"expected": expected, "results": results,
                "counts": dict(Counter(row["status"] for row in results)),
                "missing": sorted(set(expected) - set(names)), "extra": sorted(set(names) - set(expected)),
                "duplicates": {name: count for name, count in Counter(names).items() if count != 1},
                "original_order": names == expected,
                "quiet": not re.search(r"^Executing: |^Duration: |KIRK_VERBOSE=1", text, re.MULTILINE),
                "summary_runs": [int(n) for n in re.findall(r"^Runs:\s+([0-9]+)\s*$", text, re.MULTILINE)],
                "session_stopped": bool(re.search(r"^Session stopped\s*$", text, re.MULTILINE)),
                "final_success_marker": "All conformance tests passed." in text,
                "assertion_summary": {name.lower(): int(count) for name, count in re.findall(
                    r"^\s{4}(Passed|Failed|Broken|Skipped|Warnings):\s+([0-9]+)\s*$", text, re.MULTILINE)},
                "failed_or_broken": [row["name"] for row in results if row["status"] in {"fail", "broken"}],
                "pipe13": [row for row in results if row["name"] == "pipe13"]}
    coverage["complete_original_cases"] = (len(names) == len(set(names)) == 71 and names == expected
                                            and coverage["quiet"] and coverage["summary_runs"] == [71]
                                            and coverage["session_stopped"] and len(coverage["pipe13"]) == 1
                                            and set(coverage["assertion_summary"]) == {"passed", "failed", "broken", "skipped", "warnings"}
                                            and coverage["pipe13"][0]["status"] in {"pass", "fail", "broken"})
    coverage["all_tests_passed"] = (coverage["complete_original_cases"] and not coverage["failed_or_broken"]
                                       and coverage["final_success_marker"] and all(
                                           coverage["assertion_summary"][key] == 0 for key in ["failed", "broken", "warnings"]))
    save(output, coverage)
    return coverage


def verify_source(version):
    checkout = CHECKOUTS / version
    _, actual = command(["git", "-c", f"safe.directory={checkout}", "-C", str(checkout), "rev-parse", "HEAD"])
    assert actual.strip() == CONTRACT["source_shas"][version]
    for row in CONTRACT["unchanged_source_input_files"]:
        assert digest(checkout / row["path"]) == row["sha256"], row["path"]
    return checkout


def verify_sdk(log):
    text = clean_text(log.read_text())
    start = text.index("Installing cargo-osdk")
    end = text.index("Finished `release`", start)
    section = text[start:end]
    packages = sorted((name, version, path or None) for name, version, path in re.findall(
        r"^\s+Compiling ([A-Za-z0-9_-]+) v([^\s]+)(?: \(([^)]+)\))?\s*$", section, re.MULTILINE))
    expected = sorted((row["name"], row["version"], row["path"]) for row in CONTRACT["sdk"]["compiled_packages"])
    assert packages == expected, {"missing": sorted(set(expected) - set(packages)), "extra": sorted(set(packages) - set(expected))}
    assert re.search(r"Locking 153 packages to latest Rust 1\.99\.0-nightly compatible versions", section)
    assert "Installing /github/home/.cargo/bin/cargo-osdk" in text
    return {"resolver_package_count": 153, "compiled_packages_verified": len(packages),
            "original_complete_resolved_lock_available": False, "profile": "release", "locked": False}


def verify_kernel(log):
    text = clean_text(log.read_text())
    section = text[text.index("Finished `release`"):]
    packages = sorted((name, version, source or "") for name, version, source in re.findall(
        r"^\s+Compiling ([A-Za-z0-9_-]+) v([^\s]+)(?: \(([^)]+)\))?\s*$", section, re.MULTILINE))
    expected = sorted((row["name"], row["version"], row["source"] or "")
                      for row in CONTRACT["kernel_compilation"]["compiled_packages"])
    assert packages == expected, {"missing": sorted(set(expected) - set(packages)), "extra": sorted(set(packages) - set(expected))}
    assert re.search(r"Locking 203 packages to latest Rust 1\.99\.0-nightly compatible versions", section)
    assert "Finished `dev` profile [unoptimized + debuginfo]" in section
    return {"resolver_package_count": 203, "compiled_packages_verified": len(packages),
            "original_complete_resolved_lock_available": False}


def require_cached_build(log):
    text = clean_text(log.read_text())
    assert not re.search(r"^\s+Compiling |^\s+Locking [0-9]+ packages", text, re.MULTILINE)
    assert "Finished `dev` profile [unoptimized + debuginfo]" in text


def environment(version, probe=False):
    script = "python3 - <<'PY'\nprobe=" + repr(probe) + "\n" + r'''
import hashlib,json,os,pathlib,subprocess,time
home=pathlib.Path('/github/home')
sdk=home/'.cargo/bin/cargo-osdk'
keys=['HOME','RUSTUP_HOME','PATH','LD_LIBRARY_PATH','VDSO_LIBRARY_DIR','GVISOR_PREBUILT_DIR','CI','GITHUB_ACTIONS','GITHUB_WORKSPACE','SHARD']
forbidden=['CARGO_HOME','CARGO_TARGET_DIR','CMDLINE','KIRK_VERBOSE','CARGO_OSDK','RUSTFLAGS','RUSTC_WRAPPER','OSTD_TASK_STACK_SIZE_IN_PAGES','NIX_REMOTE']
data={'env':{k:os.environ.get(k) for k in keys},'unexpected_overrides':{k:os.environ[k] for k in forbidden if k in os.environ},'cwd':os.getcwd(),'home_uid':home.stat().st_uid,'sdk_path':str(sdk),'sdk_exists':sdk.is_file(),'home_cargo_symlink':(home/'.cargo').is_symlink()}
if sdk.is_file():
 data['sdk_sha256']=hashlib.sha256(sdk.read_bytes()).hexdigest()
 if probe:
  observed=set()
  reader,writer=os.pipe();os.set_blocking(writer,False);filled=0
  while True:
   try:filled+=os.write(writer,b'\0'*4096)
   except BlockingIOError:break
  os.set_blocking(writer,True)
  process=subprocess.Popen(['cargo','osdk','--version'],stdout=writer,stderr=subprocess.PIPE)
  os.close(writer)
  deadline=time.monotonic()+5
  while process.poll() is None and time.monotonic()<deadline and str(sdk) not in observed:
   pids=[process.pid]
   try:pids += [int(pid) for pid in pathlib.Path(f'/proc/{process.pid}/task/{process.pid}/children').read_text().split()]
   except FileNotFoundError:pass
   for pid in pids:
    try:observed.add(os.readlink(f'/proc/{pid}/exe'))
    except FileNotFoundError:pass
  remaining=filled
  while remaining:remaining-=len(os.read(reader,min(remaining,4096)))
  try:_,stderr=process.communicate(timeout=10)
  except subprocess.TimeoutExpired:
   process.kill();process.communicate();os.close(reader);raise
  stdout=b''
  while True:
   block=os.read(reader,4096)
   if not block:break
   stdout+=block
  os.close(reader)
  assert process.returncode==0,(process.returncode,stderr.decode(errors='replace'))
  data.update(sdk_version=stdout.decode().strip(),sdk_version_stderr=stderr.decode(),observed_preflight_executables=sorted(observed),preflight_stdout_backpressure_only=True)
  assert str(sdk) in observed,observed
data['cgroups']={name:(pathlib.Path('/sys/fs/cgroup')/name).read_text() for name in ['cpu.max','cpuset.cpus.effective','memory.max','cpu.stat'] if (pathlib.Path('/sys/fs/cgroup')/name).exists()}
print(json.dumps(data))
PY'''
    _, text = docker(version, script)
    data = json.loads(text)
    expected = CONTRACT["container"]["env"]
    for key, value in expected.items():
        assert data["env"][key] == value, (key, data["env"][key], value)
    assert data["env"]["GITHUB_WORKSPACE"] == WORKSPACE
    assert not data["unexpected_overrides"] and data["cwd"] == WORKSPACE
    assert data["home_uid"] == 1001 and not data["home_cargo_symlink"]
    return data


def prepare():
    state = load()
    assert Path("/proc/sys/kernel/random/boot_id").read_text().strip() == state["host_boot_id"]
    assert os.getuid() == 1001
    image = CONTRACT["image"]["tag"].split(":")[0] + "@" + CONTRACT["image"]["digest"]
    command(["docker", "pull", image], OUTPUT / "preparation/image-pull.log")
    _, inspected = command(["docker", "image", "inspect", image])
    inspect = json.loads(inspected)[0]
    assert image in inspect["RepoDigests"]
    env_values = dict(row.split("=", 1) for row in inspect["Config"]["Env"])
    allowed = set(CONTRACT["container"]["env"]) | set(CONTRACT["container"]["must_remain_unset"])
    save(OUTPUT / "preparation/image.json", {"Id": inspect["Id"], "RepoDigests": inspect["RepoDigests"],
                                            "Architecture": inspect["Architecture"], "Os": inspect["Os"],
                                            "environment_allowlist": {k: v for k, v in env_values.items() if k in allowed}})
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    assert os.environ.get("ImageVersion") == "20261111.196.1", os.environ.get("ImageVersion")
    assert release["VERSION_ID"].strip('"') == "26.04" and "26.04.1 LTS" in release["PRETTY_NAME"]
    host = {"host_boot_id": state["host_boot_id"], "uname": command(["uname", "-a"])[1],
            "os_release": release, "ImageVersion": os.environ["ImageVersion"],
            "lscpu": command(["lscpu"])[1], "meminfo": Path("/proc/meminfo").read_text(),
            "df": command(["df", "-h", str(CHECKOUTS)])[1],
            "workspace_disk_free_bytes": shutil.disk_usage(CHECKOUTS).free,
            "cpu_limits": {name: (Path("/sys/fs/cgroup") / name).read_text() for name in
                           ["cpu.max", "cpuset.cpus.effective", "memory.max", "cpu.stat"]
                           if (Path("/sys/fs/cgroup") / name).exists()}}
    save(OUTPUT / "preparation/host.json", host)
    # The original Make workflow creates 5 GiB of media per source, before build outputs.
    assert host["workspace_disk_free_bytes"] >= 10 * 1024 ** 3, "Insufficient space for the original two-source media."
    _, nix_size = command(["docker", "run", "--rm", "--entrypoint", "du", image, "-sx", "-B1", "/nix"])
    host["nix_copy_up_physical_bytes"] = int(nix_size.split()[0])
    save(OUTPUT / "preparation/host.json", host)
    assert host["workspace_disk_free_bytes"] >= 10 * 1024 ** 3 + host["nix_copy_up_physical_bytes"], "Insufficient space for private Nix copy-up and the original media."
    network = f"dropguard-quiet-{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    state.update(network=network, nix_volume=network + "-nix")
    save(STATE, state)
    command(["docker", "network", "create", network])
    command(["docker", "volume", "create", "--label",
             f"asterinas.dropguard-quiet={os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}", state["nix_volume"]])
    save(OUTPUT / "preparation/nix-volume.json", {"name": state["nix_volume"], "initial_copy_up_image": image,
                                               "use": "Shared mutable store and database for serial Nix builds. Cargo HOME and kernel targets are separate. No garbage collection is invoked."})
    for version in ["baseline", "candidate"]:
        checkout = verify_source(version)
        home = OUTPUT.parent / (OUTPUT.name + "-homes") / version
        home.mkdir(parents=True)
        assert not any(home.iterdir()) and home.stat().st_uid == 1001
        create = ["docker", "create", "--name", container_name(version), "--network", network,
                  "--privileged", "-v", "/dev:/dev", "-v", f"{checkout}:{WORKSPACE}",
                  "-v", f"{home}:/github/home", "-v", f"{OUTPUT}:/quiet-evidence:ro",
                  "-v", f"{OUTPUT / 'preparation/disks'}:/quiet-snapshots",
                  "-v", f"{HERE}:/quiet-controller:ro", "-v", f"{state['nix_volume']}:/nix",
                  "--workdir", WORKSPACE, "--entrypoint", "tail",
                  "--label", f"asterinas.dropguard-quiet={os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"]
        for key, value in {"HOME": "/github/home", "RUSTUP_HOME": "/root/.rustup", "SHARD": "1",
                           "CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_WORKSPACE": WORKSPACE,
                           "SOURCE_SHA": CONTRACT["source_shas"][version]}.items():
            create += ["-e", f"{key}={value}"]
        create += [image, "-f", "/dev/null"]
        command(create)
        command(["docker", "start", container_name(version)])
        # This private Git setting matches checkout's safe-directory setup.
        docker(version, f"git config --global --add safe.directory {WORKSPACE}")
        save(OUTPUT / f"preparation/{version}/environment-before.json", environment(version))
        try:
            docker(version, f"make kernel {PARAMETERS} |& tee original-build.log", timeout=45 * 60, inherit=True)
        finally:
            if (checkout / "original-build.log").exists():
                shutil.copy2(checkout / "original-build.log", OUTPUT / f"preparation/{version}/original-build.log")
        sdk = verify_sdk(checkout / "original-build.log")
        kernel = verify_kernel(checkout / "original-build.log")
        save(OUTPUT / f"preparation/{version}/nix-full.json", nix_metadata(version, "full"))
        raw = container_read(version, "test/initramfs/build/initramfs/opt/ltp/runtest/syscalls")
        assert hashlib.sha256(raw).hexdigest() == CONTRACT["runtest"]["full_bytes_sha256"]
        names = [line.split()[0] for line in raw.decode().splitlines()]
        assert len(names) == len(set(names)) == 563
        assert names[1::8] == CONTRACT["runtest"]["selected_names"]
        manifest = {"source_sha": CONTRACT["source_shas"][version],
                                               "full_set_sha256": CONTRACT["runtest"]["full_names_sha256"],
                                               "original_names": names, "all_groups": [names[index::8] for index in range(8)],
                                               "shard": 1, "selected": names[1::8],
                                               "grouping_limit": "Eight fresh boots cover every original case; this does not prove a single uninterrupted sequence."}
        docker(version, "cp test/initramfs/build/initramfs/opt/ltp/runtest/syscalls original-runtest.txt")
        docker(version, "python3 - <<'PY'\nimport json,pathlib\nmanifest=" + repr(manifest)
               + "\npathlib.Path('case-manifest.json').write_text(json.dumps(manifest,indent=2)+'\\n')"
               + "\npathlib.Path('expected-runtest.txt').write_bytes(" + repr(EXPECTED) + ")\nPY")
        selector = ",".join(names[1::8])
        try:
            docker(version, f'make kernel {PARAMETERS} CONFORMANCE_TEST_SELECTOR="{selector}" |& tee selected-build.log',
                   timeout=45 * 60, env={"CONFORMANCE_TEST_SELECTOR": selector}, inherit=True)
        finally:
            if (checkout / "selected-build.log").exists():
                shutil.copy2(checkout / "selected-build.log", OUTPUT / f"preparation/{version}/selected-build.log")
        require_cached_build(checkout / "selected-build.log")
        assert container_read(version, "test/initramfs/build/initramfs/opt/ltp/runtest/syscalls") == EXPECTED
        docker(version, "cp test/initramfs/build/initramfs/opt/ltp/runtest/syscalls selected-runtest.txt")
        nix = nix_metadata(version, "selected")
        nix["original_outer_path_matches"] = {name: nix[name] == target
                                               for name, target in CONTRACT["original_selected_initramfs"].items()}
        save(OUTPUT / f"preparation/{version}/nix-selected.json", nix)
        disk_records = media_metadata(version)
        docker(version, f"mkdir -p /quiet-snapshots/{version}")
        for item in disk_records:
            docker(version, f"cp --sparse=always --preserve=timestamps {item['file']} /quiet-snapshots/{version}/{Path(item['file']).name}")
            assert digest(OUTPUT / f"preparation/disks/{version}/{Path(item['file']).name}") == item["sha256"]
        for name in ["original-build.log", "selected-build.log", "original-runtest.txt", "selected-runtest.txt", "expected-runtest.txt", "case-manifest.json"]:
            shutil.copy2(checkout / name, OUTPUT / f"preparation/{version}/{name}")
        after = environment(version, probe=True)
        assert after["sdk_exists"]
        assert after["sdk_version"].endswith("0.19.0")
        save(OUTPUT / f"preparation/{version}/sdk.json", {**sdk, **after, "lookup_basis": "A preflight /proc executable observer saw the real HOME binary during cargo osdk --version. No PATH override is used, and the observer is absent from the guest run."})
        save(OUTPUT / f"preparation/{version}/disks.json", disk_records)
        save(OUTPUT / f"preparation/{version}/kernel-build.json", kernel)
        command(["docker", "exec", container_name(version), "cat", "/github/home/.cargo/bin/cargo-osdk"],
                OUTPUT / f"preparation/{version}/cargo-osdk")
        assert digest(OUTPUT / f"preparation/{version}/cargo-osdk") == after["sdk_sha256"]
        _, locks = docker(version, "find target -name Cargo.lock -type f")
        for lock in locks.splitlines():
            assert lock.startswith("target/") and ".." not in Path(lock).parts
            target = OUTPUT / f"preparation/{version}/generated-locks" / lock
            command(["docker", "exec", "--workdir", WORKSPACE, container_name(version), "cat", lock], target)
    baseline_nix = json.loads((OUTPUT / "preparation/baseline/nix-selected.json").read_text())
    candidate_nix = json.loads((OUTPUT / "preparation/candidate/nix-selected.json").read_text())
    for key in ["directory", "image", "directory_nar_sha256", "image_sha256", "image_nar_sha256", "host_resolv_conf", "guest_resolv_conf"]:
        assert baseline_nix[key] == candidate_nix[key], (key, baseline_nix[key], candidate_nix[key])
    command(["docker", "exec", container_name("baseline"), "cat", baseline_nix["image"]],
            OUTPUT / "preparation/common-initramfs.cpio.gz")
    assert digest(OUTPUT / "preparation/common-initramfs.cpio.gz") == baseline_nix["image_sha256"]
    # Both formats are preserved above; each guest uses the same Make-generated media.
    docker("baseline", "mkdir -p /quiet-snapshots/common")
    for row in json.loads((OUTPUT / "preparation/baseline/disks.json").read_text()):
        name = Path(row["file"]).name
        docker("baseline", f"cp --sparse=always --preserve=timestamps /quiet-snapshots/baseline/{name} /quiet-snapshots/common/{name}")
        assert digest(OUTPUT / "preparation/disks/common" / name) == row["sha256"]
    save(OUTPUT / "preparation/common-disks.json", json.loads((OUTPUT / "preparation/baseline/disks.json").read_text()))
    state["prepared"] = True
    state["rounds"] = []
    save(STATE, state)


def round_run(number):
    state = load()
    assert state.get("prepared") and Path("/proc/sys/kernel/random/boot_id").read_text().strip() == state["host_boot_id"]
    recover_interrupted(state)
    version = ["baseline", "candidate", "candidate", "baseline"][(number - 1) % 4]
    directory = OUTPUT / f"round-{number:02d}-{version}"
    directory.mkdir(parents=True)
    if number % 2 == 1 and not state.get("stopped"):
        remaining = state["deadline_monotonic"] - time.monotonic()
        if remaining < 175 * 60:
            state.update(stopped=True, stop_reason="Less than the reserved 175 minutes for another complete pair.")
    if state.get("stopped"):
        row = {"round": number, "version": version, "status": "not_run", "reason": state["stop_reason"]}
        save(directory / "round.json", row)
        save(STATE, state)
        return 0
    checkout = CHECKOUTS / version
    row = {"round": number, "pair": (number + 1) // 2, "version": version,
           "source_sha": CONTRACT["source_shas"][version], "status": "started",
           "guest_attempted": False, "contract_valid": False,
           "started_monotonic": time.monotonic(), "host_boot_id": state["host_boot_id"]}
    state["rounds"].append(row)
    save(directory / "round.json", row)
    save(STATE, state)
    try:
        verify_source(version)
        before = environment(version)
        expected_sdk = json.loads((OUTPUT / f"preparation/{version}/sdk.json").read_text())
        assert before["sdk_sha256"] == expected_sdk["sdk_sha256"]
        for other in ["baseline", "candidate"]:
            _, processes = command(["docker", "top", container_name(other), "-eo", "comm,args"])
            assert not re.search(r"^(?:qemu-system|cargo|rustc)\S*\s", processes, re.MULTILINE), processes
        records = json.loads((OUTPUT / "preparation/common-disks.json").read_text())
        for item in records:
            path = item["file"]
            docker(version, f'cp --preserve=timestamps /quiet-evidence/preparation/disks/common/{Path(path).name} {path}')
        assert media_metadata(version) == records
        expected_nix = json.loads((OUTPUT / f"preparation/{version}/nix-selected.json").read_text())
        actual_nix = nix_metadata(version, "selected")
        for key in ["directory", "image", "directory_nar_sha256", "image_sha256", "image_nar_sha256", "host_resolv_conf", "guest_resolv_conf"]:
            assert actual_nix[key] == expected_nix[key], key
        save(directory / "nix-before.json", actual_nix)
        for name in ["qemu.log", "qemu-serial.log", "selected-run.log", "case-coverage.json"]:
            (checkout / name).unlink(missing_ok=True)
        selector = ",".join(CONTRACT["runtest"]["selected_names"])
        pipeline = f'timeout --signal=TERM --kill-after=30s 80m make run_kernel {PARAMETERS} CONFORMANCE_TEST_SELECTOR="{selector}" |& tee selected-run.log'
        status_path = f"/tmp/dropguard-quiet-round-{number:02d}.status"
        script = ("set +e\n" + pipeline + "\nstatuses=(\"${PIPESTATUS[@]}\")\n"
                  + f'printf "%s %s\\n" "${{statuses[0]}}" "${{statuses[1]}}" > {status_path}\n'
                  + 'if (( statuses[1] != 0 )); then exit "${statuses[1]}"; fi\nexit "${statuses[0]}"')
        row.update(run_command=pipeline, guest_attempted=True, contract_valid=True)
        save(directory / "round.json", row)
        save(STATE, state)
        code, _ = docker(version, script, check=False, env={"CONFORMANCE_TEST_SELECTOR": selector}, inherit=True)
        row["make_pipeline_exit_code"] = code
        try:
            statuses = container_read(version, status_path).decode().split()
            assert len(statuses) == 2, statuses
            row.update(make_or_timeout_exit_code=int(statuses[0]), tee_exit_code=int(statuses[1]))
        except Exception as error:
            row["exit_capture_error"] = str(error)
        coverage = collect_round(version, directory, row)
        row["status"] = "completed" if coverage["complete_original_cases"] else "test_incomplete"
        if "exit_capture_error" in row:
            row["status"] = "collection_error"
        actual = container_read(version, "test/initramfs/build/initramfs/opt/ltp/runtest/syscalls")
        if actual != EXPECTED:
            raise ContractMismatch("The actual runtime command file differs from the original 71 bytes.")
        text = clean_text((checkout / "selected-run.log").read_text())
        if re.search(r"^\s+Compiling |^\s+Locking [0-9]+ packages", text, re.MULTILINE):
            raise ContractMismatch("The original cached run rebuilt packages or resolved new dependencies.")
        (directory / "actual-runtest.txt").write_bytes(actual)
    except Exception as error:
        if row["guest_attempted"] and "counts" not in row:
            try: collect_round(version, directory, row)
            except Exception as cleanup_error:
                row["collection_error"] = str(cleanup_error)
                state.update(stopped=True, stop_reason=f"Could not confirm residual guest cleanup after round {number}.")
        if not row["guest_attempted"] or isinstance(error, ContractMismatch):
            row.update(status="protocol_error", contract_valid=False)
            state.update(stopped=True, stop_reason=f"The quiet contract was not satisfied in round {number}: {error}")
        else:
            row["status"] = "collection_error"
        row.update(error=str(error), ended_monotonic=time.monotonic())
    finally:
        save(directory / "round.json", row)
        save(STATE, state)
    return 0 if row["status"] == "completed" and row.get("make_pipeline_exit_code") == row.get("strict_verifier_exit_code") == 0 else 1


def finalize():
    state = load()
    recover_interrupted(state)
    completed = [row for row in state.get("rounds", []) if row["status"] == "completed" and row.get("complete_original_cases")]
    attempted = [row for row in state.get("rounds", []) if row["guest_attempted"]]
    versions = {}
    for version in ["baseline", "candidate"]:
        rows = [row for row in state.get("rounds", []) if row["version"] == version]
        observed = [row for row in rows if len(row.get("pipe13", [])) == 1]
        versions[version] = {"round_attempts": len(rows), "guest_attempted": sum(row["guest_attempted"] for row in rows),
                             "completed_original_rounds": sum(row in completed for row in rows),
                             "pipe13_observed": len(observed),
                             "pipe13": dict(Counter(row["pipe13"][0]["status"] for row in observed)),
                             "pipe13_unobserved_after_guest_attempt": sum(row["guest_attempted"] and not row.get("pipe13") for row in rows),
                             "all_results": rows}
    summary = {"source_shas": CONTRACT["source_shas"], "planned_maximum_rounds": 24,
               "minimum_rounds": 12, "completed_original_rounds": len(completed),
               "minimum_reached": len(completed) >= 12, "versions": versions,
               "unrun_rounds": [number for number in range(1, 25) if number not in {row["round"] for row in state.get("rounds", []) if row["guest_attempted"]}],
               "stop_reason": state.get("stop_reason"),
               "comparison_data_complete": len(completed) >= 12 and len(completed) % 2 == 0 and len(completed) == len(state.get("rounds", [])),
               "all_test_runs_passed": bool(attempted) and all(
                   row["status"] == "completed" and row.get("complete_original_cases")
                   and row.get("make_pipeline_exit_code") == row.get("strict_verifier_exit_code") == 0
                   for row in attempted),
               "interpretation_limit": "No pipe13 reproduction is not proof that the patch is unrelated. Compare actual failure counts; retain the original broken observation and every other failure."}
    save(OUTPUT / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0 if summary["comparison_data_complete"] and summary["all_test_runs_passed"] and not state.get("stopped") else 1


def cleanup():
    label = f"{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    if STATE.exists():
        recover_interrupted(load())
    for version in ["baseline", "candidate"]:
        name = container_name(version)
        code, output = command(["docker", "inspect", "--format", '{{ index .Config.Labels "asterinas.dropguard-quiet" }}', name], check=False)
        if code == 0:
            assert output.strip() == label
            command(["docker", "rm", "-f", name])
    if STATE.exists() and load().get("network"):
        command(["docker", "network", "rm", load()["network"]], check=False)
    if STATE.exists() and load().get("nix_volume"):
        name = load()["nix_volume"]
        code, output = command(["docker", "volume", "inspect", "--format", '{{ index .Labels "asterinas.dropguard-quiet" }}', name], check=False)
        if code == 0:
            assert output.strip() == label
            command(["docker", "volume", "rm", name])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "round", "finalize", "cleanup", "parse"])
    parser.add_argument("value", nargs="?")
    parser.add_argument("output", nargs="?")
    args = parser.parse_args()
    if args.phase == "parse":
        coverage = parse_quiet(Path(args.value), Path(args.output))
        return 0 if coverage["all_tests_passed"] else 1
    if args.phase == "prepare": prepare(); return 0
    if args.phase == "round": return round_run(int(args.value))
    if args.phase == "finalize": return finalize()
    cleanup(); return 0


if __name__ == "__main__":
    sys.exit(main())
