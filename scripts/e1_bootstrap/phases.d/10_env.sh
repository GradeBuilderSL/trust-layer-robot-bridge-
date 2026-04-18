#!/usr/bin/env bash

bootstrap_log "env file: ${BOOTSTRAP_ENV_FILE}"
bootstrap_log "transport: ${E1_TRANSPORT:-noetix_dds}"
bootstrap_log "server port: ${E1_SERVER_PORT:-8083}, bridge port: ${BRIDGE_PORT:-8080}"
