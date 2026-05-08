#!/usr/bin/env bash
# Run ONCE on the Pi to set up venv with system-site-packages so picamera2 is importable.
set -e
cd "$(dirname "$0")/.."

# System packages we depend on
sudo apt-get update
sudo apt-get install -y python3-picamera2 libportaudio2 python3-venv

# Venv with system-site-packages so picamera2 is visible
python3 -m venv --system-site-packages .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

echo "Setup complete. Activate with: source .venv/bin/activate"
