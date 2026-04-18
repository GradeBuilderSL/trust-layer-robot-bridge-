#!/usr/bin/env bash

if [ "${E1_BOOTSTRAP_INSTALL_DEPS}" != "1" ]; then
    bootstrap_log "skip Python deps"
    exit 0
fi

bootstrap_log "installing Python requirements"
python3 -m pip install -r "${BOOTSTRAP_REPO_ROOT}/requirements.txt"
