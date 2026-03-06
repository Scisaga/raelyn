#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

echo "[bootstrap-ubuntu] installing system packages for python bootstrap (requires sudo)"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip curl ca-certificates

echo "[bootstrap-ubuntu] bootstrapping project venv"
./scripts/dev/bootstrap-python.sh

echo "[bootstrap-ubuntu] done"
