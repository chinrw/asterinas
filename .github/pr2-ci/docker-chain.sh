#!/bin/bash
# SPDX-License-Identifier: MPL-2.0
set -euo pipefail
logs=/tmp/pr2-ci
mkdir -p "$logs"
tag="pr2-ci-${GITHUB_RUN_ID:-local}"
rust_version=$(python3 -c 'import tomllib; print(tomllib.load(open("rust-toolchain.toml", "rb"))["toolchain"]["channel"])')
cat > "$logs/cold.Dockerfile" <<'DOCKER'
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates xz-utils bzip2
RUN bash -c 'sh <(curl -L https://nixos.org/nix/install) --daemon --yes'
ENV PATH="/nix/var/nix/profiles/default/bin:${PATH}"
DOCKER
docker build --progress plain -t pr2-cold-nix -f "$logs/cold.Dockerfile" . 2>&1 | tee "$logs/cold-bootstrap.log"
docker run --rm -i --mount "type=bind,src=$PWD,dst=/source,readonly" --entrypoint /bin/bash pr2-cold-nix < .github/pr2-ci/cold-qemu.sh 2>&1 | tee "$logs/cold-qemu.log"
for image in osdk-dev prebuilt-nix-packages kernel-dev dev; do
    case "$image" in
        osdk-dev) dockerfile=osdk/tools/docker/Dockerfile ;;
        prebuilt-nix-packages) dockerfile=tools/dev_env/docker/prebuilt-nix-packages/Dockerfile ;;
        kernel-dev) dockerfile=tools/dev_env/docker/kernel-dev/Dockerfile ;;
        dev) dockerfile=tools/dev_env/docker/Dockerfile ;;
    esac
    docker build --progress plain --build-arg "BASE_VERSION=$tag" --build-arg "ASTER_RUST_VERSION=$rust_version" \
        --iidfile "$logs/$image.id" -t "asterinas/$image:$tag" -f "$dockerfile" . 2>&1 | tee "$logs/$image-build.log"
    docker run --rm -i --network none --entrypoint /bin/bash "asterinas/$image:$tag" < .github/pr2-ci/verify-image.sh 2>&1 | tee "$logs/$image-runtime.log"
    docker run --rm --network none --entrypoint nix "asterinas/$image:$tag" \
        --extra-experimental-features nix-command path-info --all | sort > "$logs/$image-store-paths.txt"
    if [ "$image" = kernel-dev ] || [ "$image" = dev ]; then
        comm -23 "$logs/prebuilt-nix-packages-store-paths.txt" "$logs/$image-store-paths.txt" > "$logs/$image-missing-prebuilt-paths.txt"
        test ! -s "$logs/$image-missing-prebuilt-paths.txt"
    fi
    docker run --rm -i --network none --entrypoint /bin/bash "asterinas/$image:$tag" -s -- gc < .github/pr2-ci/verify-image.sh 2>&1 | tee "$logs/$image-gc.log"
done
container=pr2-kernel-validation
trap 'docker rm -f "$container" >/dev/null 2>&1 || true' EXIT
docker run -d --name "$container" --mount "type=bind,src=$PWD,dst=/root/asterinas" \
    --workdir /root/asterinas --entrypoint /bin/bash "asterinas/kernel-dev:$tag" -c 'sleep infinity'
docker exec "$container" make kernel 2>&1 | tee "$logs/kernel.log"
docker exec "$container" make run_kernel AUTO_TEST=boot ENABLE_KVM=0 2>&1 | tee "$logs/boot.log"
grep -F 'Successfully booted.' "$logs/boot.log"
