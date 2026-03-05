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

# Recover orphaned running jobs before any worker starts claiming new work.
python -m raelyn.recover_orphan_jobs || true

# API
start python -m raelyn.api_server

# Workers (separate roles to avoid one queue starving others)
start env WORKER_ROLE=download_youtube python -m raelyn.worker
start env WORKER_ROLE=download_bilibili python -m raelyn.worker
start env WORKER_ROLE=audio python -m raelyn.worker
start env WORKER_ROLE=process python -m raelyn.worker
start env WORKER_ROLE=asr python -m raelyn.worker
start env WORKER_ROLE=sync python -m raelyn.worker
start env WORKER_ROLE=ai python -m raelyn.worker

# Scheduler
start python -m raelyn.scheduler

wait -n
shutdown
exit 1
