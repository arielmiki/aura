#!/usr/bin/env bash
# One-time Mac setup for running pi-rocky locally on a laptop instead of the Pi.
set -e
cd "$(dirname "$0")/.."

# PortAudio is the runtime dep of sounddevice
if ! brew list portaudio &>/dev/null; then
    brew install portaudio
fi

if [ ! -d .venv-mac ]; then
    python3 -m venv .venv-mac
fi
. .venv-mac/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install opencv-python  # cv2 backend for camera; not needed on Pi

cat <<'EOF'

Mac setup complete. Activate the venv with:
  . .venv-mac/bin/activate

Then create .env with:
  cp .env.example .env
  # edit .env: paste your GEMINI_API_KEY, set ROCKY_MIC_DEVICE= and ROCKY_SPEAKER_DEVICE= to empty strings for system defaults

Run:
  python rocky.py
EOF
