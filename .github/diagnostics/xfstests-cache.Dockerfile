# syntax=docker/dockerfile:1
# SPDX-License-Identifier: MPL-2.0

FROM asterinas/kernel-dev@sha256:3d9970e695db7f769ef3eb135c7cd561c3f4b86605785854374c807380480d26

ARG SOURCE_REVISION
LABEL org.opencontainers.image.source="https://github.com/chinrw/asterinas"
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ENV NIX_CONFIG="sandbox = false"

# A bind mount keeps validation sources and intermediate builds out of the image.
RUN --mount=type=bind,target=/mnt/source,readonly \
    SOURCE_REVISION="${SOURCE_REVISION}" \
    bash /mnt/source/.github/diagnostics/xfstests-cache-build.sh
