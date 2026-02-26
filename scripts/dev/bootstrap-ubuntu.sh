#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

echo "[bootstrap-ubuntu] installing system packages (requires sudo)"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip ffmpeg curl ca-certificates

echo "[bootstrap-ubuntu] creating venv"
python3 -m venv .venv

echo "[bootstrap-ubuntu] installing python deps"
./.venv/bin/python -m pip install -r backend/requirements.txt

echo "[bootstrap-ubuntu] done"
