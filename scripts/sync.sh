#!/usr/bin/env bash
# Push local code to the Pi over Tailscale.
set -e
cd "$(dirname "$0")/.."
rsync -av --delete \
  --exclude=.git --exclude=.venv --exclude=__pycache__ \
  --exclude='*.wav' --exclude='*.jpg' --exclude=vocab.json \
  --exclude=docs \
  ./ me322@pibot:/home/me322/pi-rocky/
echo "Synced."
