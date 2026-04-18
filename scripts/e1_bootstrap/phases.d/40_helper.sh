#!/usr/bin/env bash

if [ "${E1_BOOTSTRAP_BUILD_HELPER}" != "1" ]; then
    bootstrap_log "skip native helper build"
    exit 0
fi

if ! sdk_root="$(bootstrap_find_sdk_root)"; then
    bootstrap_log "SDK root not found; skip native helper build"
    exit 0
fi

export E1_SDK_ROOT="${sdk_root}"
bootstrap_log "using SDK ${E1_SDK_ROOT}"

if ! command -v cmake >/dev/null 2>&1; then
    if [ -x /opt/cmake-3.31.8-linux-aarch64/bin/cmake ]; then
        export PATH="/opt/cmake-3.31.8-linux-aarch64/bin:${PATH}"
    fi
fi

if ! command -v cmake >/dev/null 2>&1; then
    bootstrap_log "cmake not found; skip helper build"
    exit 0
fi

chmod +x "${BOOTSTRAP_REPO_ROOT}/scripts/"*.sh || true
cmake -S "${BOOTSTRAP_REPO_ROOT}/native" -B "${BOOTSTRAP_REPO_ROOT}/native/build" -DE1_SDK_ROOT="${E1_SDK_ROOT}"
cmake --build "${BOOTSTRAP_REPO_ROOT}/native/build" -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
