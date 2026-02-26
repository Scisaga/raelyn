#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source scripts/dev/load-env.sh
fi

export PYTHONPATH="$(pwd)/backend"
PY="${PY:-python3}"
if [[ -x .venv/bin/python ]]; then PY=".venv/bin/python"; fi
exec "$PY" -m videosync.worker
