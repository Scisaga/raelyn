#!/usr/bin/env bash
set -euo pipefail

cd /app

export PYTHONPATH="/app/backend"

echo "[entrypoint] python: $(python --version)"
echo "[entrypoint] starting api + workers + scheduler (single container)"

pids=()

start() {
  "$@" &
  pids+=("$!")
}

shutdown() {
  echo "[entrypoint] shutting down..."
  for pid in "${pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait || true
}

trap shutdown SIGINT SIGTERM

# API
start python -m videosync.api_server

# Workers (separate roles to avoid one queue starving others)
start env WORKER_ROLE=download python -m videosync.worker
start env WORKER_ROLE=process python -m videosync.worker
start env WORKER_ROLE=sync python -m videosync.worker
start env WORKER_ROLE=ai python -m videosync.worker

# Scheduler
start python -m videosync.scheduler

wait -n
shutdown
exit 1

