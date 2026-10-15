#!/bin/bash
# SPDX-License-Identifier: MPL-2.0

set -euxo pipefail
export LC_ALL=C
logs="${RUNNER_TEMP:-$(mktemp -d /tmp/series-docker-images.XXXXXX)}/docker-images"
mkdir -p "$logs"
source_sha=$(git rev-parse HEAD)
git_mounts=()
if [[ -f .git ]]; then
    # Linked worktrees refer to Git metadata outside the source bind mount.
    git_common_dir=$(git rev-parse --path-format=absolute --git-common-dir)
    git_mounts=(--mount "type=bind,src=$git_common_dir,dst=$git_common_dir,readonly")
fi
tag="series-${source_sha:0:12}-${GITHUB_RUN_ID:-local-$(date +%s)}-${GITHUB_RUN_ATTEMPT:-1}-$(uname -m)"
rust_version=$(python3 -c 'import tomllib; print(tomllib.load(open("rust-toolchain.toml", "rb"))["toolchain"]["channel"])')
nixpkgs_url=$(python3 - <<'PY'
import json
lock = json.load(open("flake.lock"))
source = lock["nodes"][lock["nodes"][lock["root"]]["inputs"]["nixpkgs"]]["locked"]
print(f"https://github.com/{source['owner']}/{source['repo']}/archive/{source['rev']}.tar.gz")
PY
)
if [[ "${GITHUB_ACTIONS:-false}" == true ]]; then
    test "$GITHUB_REPOSITORY" = chinrw/asterinas
    test "$GITHUB_REF" = refs/heads/test/nix-series-20261015
fi
devices=()
enable_kvm=0
case "$(uname -m)" in
    x86_64)
        architecture=amd64
        if [[ "${GITHUB_ACTIONS:-false}" == true ]]; then
            test -c /dev/kvm
        fi
        if [[ -c /dev/kvm ]]; then
            enable_kvm=1
            devices=(--device /dev/kvm)
        fi
        ;;
    aarch64) architecture=arm64 ;;
    *) exit 1 ;;
esac
{
    git rev-parse HEAD 'HEAD^{tree}'
    git status --short
    sha256sum flake.lock osdk/tools/docker/Dockerfile tools/dev_env/nix/overlay.nix \
        tools/dev_env/nix/packages/{grub,ovmf,edk2,dev-tools}.nix
    uname -a
    docker version
    docker buildx version
    printf 'Workflow: %s\nRunner image: %s %s\nRust: %s\nKVM: %s\n' \
        "${GITHUB_WORKFLOW_SHA:-local}" "${ImageOS:-unknown}" "${ImageVersion:-unknown}" "$rust_version" "$enable_kvm"
} > "$logs/identity.txt"
git diff --binary HEAD > "$logs/source.diff"
sha256sum flake.lock > "$logs/lock.sha256"
printf 'TDX hardware boot is not covered.\n' > "$logs/not-covered.txt"
df -h
parent=
for image in osdk-dev prebuilt-nix-packages kernel-dev dev; do
    case "$image" in
        osdk-dev) dockerfile=osdk/tools/docker/Dockerfile ;;
        prebuilt-nix-packages) dockerfile=tools/dev_env/docker/prebuilt-nix-packages/Dockerfile ;;
        kernel-dev) dockerfile=tools/dev_env/docker/kernel-dev/Dockerfile ;;
        dev) dockerfile=tools/dev_env/docker/Dockerfile ;;
    esac
    if [[ -n "$parent" ]]; then
        test "$(docker image inspect --format '{{.Id}}' "asterinas/$parent:$tag")" = "$(cat "$logs/$parent.id")"
    fi
    # The default Docker driver lets each stage consume its local parent image.
    docker buildx build --builder default --load --pull=false --progress=plain --network host \
        --build-arg HTTP_PROXY --build-arg HTTPS_PROXY --build-arg ALL_PROXY --build-arg NO_PROXY \
        --build-arg http_proxy --build-arg https_proxy --build-arg all_proxy --build-arg no_proxy \
        --platform "linux/$architecture" --build-arg "BASE_VERSION=$tag" \
        --build-arg "ASTER_RUST_VERSION=$rust_version" --iidfile "$logs/$image.id" \
        --tag "asterinas/$image:$tag" --file "$dockerfile" . 2>&1 | tee "$logs/$image-build.log"
    image_id=$(cat "$logs/$image.id")
    docker image inspect "$image_id" > "$logs/$image.inspect.json"
    test "$(docker image inspect --format '{{.Architecture}}' "$image_id")" = "$architecture"
    if [[ -n "$parent" ]]; then
        jq -e --slurpfile parent "$logs/$parent.inspect.json" \
            '.[0].RootFS.Layers[:($parent[0][0].RootFS.Layers | length)] == $parent[0][0].RootFS.Layers' \
            "$logs/$image.inspect.json"
    fi
    docker run --rm --pull=never --network none --entrypoint nix "$image_id" \
        --extra-experimental-features nix-command path-info --all | sort -u > "$logs/$image.paths"
    if [[ "$image" == osdk-dev ]]; then
        # A small snapshot avoids copying local target artifacts into the store.
        docker run --rm --pull=never -i --network host \
            -e HTTP_PROXY -e HTTPS_PROXY -e ALL_PROXY -e NO_PROXY \
            -e http_proxy -e https_proxy -e all_proxy -e no_proxy \
            --mount "type=bind,src=$PWD,dst=/source,readonly" \
            --entrypoint bash "$image_id" --noprofile --norc -s \
            > "$logs/outputs.json" 2> "$logs/flake-build.log" <<'SH'
set -euxo pipefail
mkdir -p /tmp/pr5-flake/tools/dev_env
cp /source/flake.nix /source/flake.lock /source/rust-toolchain.toml /tmp/pr5-flake/
cp -r /source/tools/dev_env/nix /tmp/pr5-flake/tools/dev_env/
nix --extra-experimental-features 'nix-command flakes' \
    build --accept-flake-config --no-update-lock-file --no-link \
    path:/tmp/pr5-flake#grub path:/tmp/pr5-flake#ovmf path:/tmp/pr5-flake#dev-tools
nix --extra-experimental-features 'nix-command flakes' \
    eval --json --accept-flake-config --no-update-lock-file \
    --apply 'p: { qemu = p.qemu.outPath; qemuVersion = p.qemu.version; grub = p.grub.outPath; ovmf = p.ovmf.outPath; devTools = p.dev-tools.outPath; vdso = p.vdso.outPath; }' \
    "path:/tmp/pr5-flake#packages.$(uname -m)-linux" > /tmp/outputs.json
# The image keeps only copies of the firmware, so record the expected sizes.
# OVMF builds are not bit-reproducible, so the bytes can differ between builds.
python3 - <<'PY'
import json
outputs = json.load(open("/tmp/outputs.json"))
outputs["ovmfSizes"] = {
    name: len(open(f"{outputs['ovmf']}/{name}", "rb").read())
    for name in ["OVMF.fd", "OVMF_CODE.fd", "OVMF_VARS.fd", "microvm/MICROVM.fd"]
}
print(json.dumps(outputs))
PY
SH
        qemu=$(jq -r .qemu "$logs/outputs.json")
        qemu_version=$(jq -r .qemuVersion "$logs/outputs.json")
        grub=$(jq -r .grub "$logs/outputs.json")
        dev_tools=$(jq -r .devTools "$logs/outputs.json")
        ovmf_sizes=$(jq -c .ovmfSizes "$logs/outputs.json")
        vdso=$(jq -r .vdso "$logs/outputs.json")
    fi
    if [[ "$image" == kernel-dev || "$image" == dev ]]; then
        comm -23 "$logs/prebuilt-nix-packages.paths" "$logs/$image.paths" > "$logs/$image.missing"
        test ! -s "$logs/$image.missing"
    fi
    docker run --rm --pull=never -i --network none --entrypoint bash "$image_id" \
        --noprofile --norc -s -- "$qemu" "$qemu_version" "$grub" "$dev_tools" "$ovmf_sizes" "$vdso" "$image" no "$nixpkgs_url" \
        < tools/github_workflows/check_docker_image.sh 2>&1 | tee "$logs/$image-runtime.log"
    if [[ "$image" == osdk-dev || "$image" == dev ]]; then
        # GC must not touch the normal images or their final initramfs preheat.
        docker run --rm --pull=never -i --network none --entrypoint bash "$image_id" \
            --noprofile --norc -s -- "$qemu" "$qemu_version" "$grub" "$dev_tools" "$ovmf_sizes" "$vdso" "$image" yes "$nixpkgs_url" \
            < tools/github_workflows/check_docker_image.sh 2>&1 | tee "$logs/$image-gc.log"
    fi
    parent=$image
    df -h
done

docker run --rm --pull=never --network host \
    -e HTTP_PROXY -e HTTPS_PROXY -e ALL_PROXY -e NO_PROXY \
    -e http_proxy -e https_proxy -e all_proxy -e no_proxy \
    --entrypoint nix-shell "$(cat "$logs/dev.id")" \
    -p hello --run hello 2>&1 | tee "$logs/channel.log"

container="pr5-image-tests-$tag"
trap 'docker rm -f "$container" >/dev/null 2>&1 || true' EXIT
docker run -d --pull=never --name "$container" "${devices[@]}" --network host \
    "${git_mounts[@]}" \
    -e HTTP_PROXY -e HTTPS_PROXY -e ALL_PROXY -e NO_PROXY \
    -e http_proxy -e https_proxy -e all_proxy -e no_proxy \
    --mount "type=bind,src=$PWD,dst=/root/asterinas" --workdir /root/asterinas \
    --entrypoint bash "$(cat "$logs/osdk-dev.id")" --noprofile --norc -c 'sleep infinity'
docker exec "$container" git config --global --add safe.directory /root/asterinas
docker exec "$container" timeout 30m make install_osdk 2>&1 | tee "$logs/osdk-install.log"
# This template has no accelerator override, so the fresh project uses TCG.
set +e
docker exec -i "$container" timeout 30m bash --noprofile --norc -s <<'SH' 2>&1 | tee "$logs/osdk-new-run.log"
set -euxo pipefail
mkdir /tmp/pr5-new
cd /tmp/pr5-new
OSDK_TARGET_ARCH=x86_64 cargo osdk new --kernel pr5_kernel
cd pr5_kernel
cmp OSDK.toml /root/asterinas/osdk/src/commands/new/kernel.OSDK.toml.template
cp OSDK.toml /tmp/pr5-template.before
sha256sum /root/ovmf/release/OVMF_VARS.fd
OSDK_TARGET_ARCH=x86_64 cargo osdk run
cmp OSDK.toml /tmp/pr5-template.before
sha256sum /root/ovmf/release/OVMF_VARS.fd
python3 - <<'PY'
import shlex
import tomllib
with open("target/osdk/pr5_kernel/bundle.toml", "rb") as f:
    bundle = tomllib.load(f)
config = bundle["config"]["run"]
args = shlex.split(config["qemu"]["args"])
assert "-accel" not in args and "-enable-kvm" not in args, args
assert config["grub"]["grub_mkrescue"] == "grub-mkrescue"
assert config["boot"]["method"] == "grub-rescue-iso"
assert "if=pflash,format=raw,unit=1,file=/root/ovmf/release/OVMF_VARS.fd" in args
print("Fresh OSDK project used the unchanged template, TCG and writable pflash")
PY
SH
new_status=${PIPESTATUS[0]}
set -e
docker cp "$container:/tmp/pr5-new/pr5_kernel/OSDK.toml" "$logs/osdk-new-OSDK.toml" || true
docker cp "$container:/tmp/pr5-new/pr5_kernel/target/osdk/pr5_kernel/bundle.toml" "$logs/osdk-new-bundle.toml" || true
test "$new_status" = 0
grep -F 'Hello world from guest kernel!' "$logs/osdk-new-run.log"

# The ARM64 image's native GDB cannot debug an x86_64 guest.
if [[ "$architecture" == amd64 ]]; then
    docker exec "$container" timeout 30m make test_osdk 2>&1 | tee "$logs/osdk-test.log"
else
    echo 'Full OSDK tests are not covered on ARM64 because native GDB lacks the x86_64 target.' \
        | tee -a "$logs/not-covered.txt"
fi
docker rm -f "$container"

docker run -d --pull=never --name "$container" "${devices[@]}" --network host \
    "${git_mounts[@]}" \
    -e HTTP_PROXY -e HTTPS_PROXY -e ALL_PROXY -e NO_PROXY \
    -e http_proxy -e https_proxy -e all_proxy -e no_proxy \
    --mount "type=bind,src=$PWD,dst=/root/asterinas" --workdir /root/asterinas \
    --entrypoint bash "$(cat "$logs/kernel-dev.id")" --noprofile --norc -c 'sleep infinity'
docker exec "$container" git config --global --add safe.directory /root/asterinas
if [[ "$enable_kvm" == 1 ]]; then
    docker exec "$container" bash -euc 'test -r /dev/kvm && test -w /dev/kvm'
fi
docker exec "$container" timeout 30m make check 2>&1 | tee "$logs/check.log"
docker exec "$container" timeout 30m make test 2>&1 | tee "$logs/test.log"
docker exec "$container" timeout 30m make -C book build 2>&1 | tee "$logs/book.log"
for boot in default linux-efi-pe64 linux-efi-handover64 microvm; do
    boot_args=()
    case "$boot" in
        default) ;;
        microvm) boot_args=(SCHEME=microvm BOOT_PROTOCOL=linux-efi-pe64) ;;
        *) boot_args=("BOOT_PROTOCOL=$boot") ;;
    esac
    # Removing the previous bundle forces a new ISO and records this route's tools.
    docker exec "$container" rm -f qemu.log qemu-serial.log target/osdk/asterinas/bundle.toml
    set +e
    docker exec "$container" timeout 30m make run_kernel AUTO_TEST=boot TARGET_ARCH=x86_64 \
        NETDEV=user QEMU_DISPLAY=none "ENABLE_KVM=$enable_kvm" "${boot_args[@]}" \
        2>&1 | tee "$logs/boot-$boot.log"
    boot_status=${PIPESTATUS[0]}
    set -e
    for file in qemu.log qemu-serial.log; do
        if [[ -f "$file" ]]; then cp "$file" "$logs/boot-$boot-$file"; fi
    done
    if [[ -f target/osdk/asterinas/bundle.toml ]]; then
        cp target/osdk/asterinas/bundle.toml "$logs/boot-$boot-bundle.toml"
    fi
    test "$boot_status" = 0
    grep '^Successfully booted\.' "$logs/boot-$boot-qemu.log"
    python3 - "$logs/boot-$boot-bundle.toml" "$boot" "$enable_kvm" <<'PY'
import shlex
import sys
import tomllib
path, boot, kvm = sys.argv[1:]
with open(path, "rb") as f:
    bundle = tomllib.load(f)
config = bundle["config"]["run"]
args = shlex.split(config["qemu"]["args"])
firmware = "/root/ovmf/release/" + ("microvm/MICROVM.fd" if boot == "microvm" else "OVMF.fd")
assert args[args.index("-bios") + 1] == firmware, args
assert ("-accel" in args and args[args.index("-accel") + 1] == "kvm") == (kvm == "1"), args
assert config["boot"]["method"] == ("vmm-direct" if boot == "microvm" else "grub-rescue-iso")
expected_grub = "/usr/bin/grub-mkrescue" if boot == "linux-efi-handover64" else "grub-mkrescue"
assert config["grub"]["grub_mkrescue"] == expected_grub, config["grub"]
if boot != "microvm":
    assert config["grub"]["boot_protocol"] == ("multiboot2" if boot == "default" else "linux")
    assert "GrubIso" in bundle["vm_image"]["typ"], bundle.get("vm_image")
print(f"Validated {boot}: firmware={firmware}, grub={expected_grub}, KVM={kvm}")
PY
done
sha256sum --check "$logs/lock.sha256"
echo "Candidate image validation passed. Evidence: $logs"
