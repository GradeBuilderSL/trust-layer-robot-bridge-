#!/usr/bin/env bash

if [ "${E1_BOOTSTRAP_INSTALL_SPEECH}" != "1" ]; then
    bootstrap_log "skip speech packages"
    exit 0
fi

if command -v espeak-ng >/dev/null 2>&1; then
    bootstrap_log "espeak-ng already installed"
    exit 0
fi

if command -v sudo >/dev/null 2>&1; then
    bootstrap_log "installing speech packages"
    sudo apt-get update
    sudo apt-get install -y espeak-ng ffmpeg
else
    bootstrap_log "sudo not found; skip speech package install"
fi
