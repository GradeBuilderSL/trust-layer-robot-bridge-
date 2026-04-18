#!/usr/bin/env bash

if [ "${E1_BOOTSTRAP_START_STACK}" != "1" ]; then
    bootstrap_log "skip stack start"
    exit 0
fi

bash "${BOOTSTRAP_REPO_ROOT}/scripts/start_e1_stack.sh"
