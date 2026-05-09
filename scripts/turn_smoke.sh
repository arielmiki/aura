#!/usr/bin/env bash
# Smoke test the /turn endpoint with a generated WAV.
set -e
cd "$(dirname "$0")/.."

if [ ! -f /tmp/sample.wav ]; then
    say -o /tmp/sample.aiff -v Samantha "Hello Rocky. My dog is a corgi named Lily."
    afconvert -f WAVE -d LEI16 /tmp/sample.aiff /tmp/sample.wav
fi

echo "POSTing to /turn..."
curl -s -X POST http://localhost:8000/turn \
    -F "audio=@/tmp/sample.wav;type=audio/wav" \
    -D - \
    -o /tmp/rocky_reply.mp3

echo
echo "reply audio: $(ls -lh /tmp/rocky_reply.mp3 | awk '{print $5}')"
echo "play: afplay /tmp/rocky_reply.mp3"
