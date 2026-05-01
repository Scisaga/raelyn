#!/usr/bin/env bash
set -euo pipefail

cd /app

export PYTHONPATH="/app/backend"

echo "[entrypoint] python: $(python --version)"
echo "[entrypoint] starting api + workers + scheduler (single container)"

pids=()

YOUTUBE_DOWNLOAD_CONCURRENCY_DEFAULT=2
BILIBILI_DOWNLOAD_CONCURRENCY_DEFAULT=2
DOWNLOAD_CONCURRENCY_MIN=1
DOWNLOAD_CONCURRENCY_MAX=10
EMBEDDING_WORKER_CONCURRENCY_DEFAULT=1
ANALYSIS_WORKER_CONCURRENCY_DEFAULT=1
ASR_WORKER_CONCURRENCY_DEFAULT=1
ASR_WORKER_CONCURRENCY_MIN=1
EMBEDDING_WORKER_CONCURRENCY_MIN=0
ANALYSIS_WORKER_CONCURRENCY_MIN=0
ROLE_WORKER_CONCURRENCY_MAX=10

start() {
  "$@" &
  pids+=("$!")
}

clamp_download_concurrency() {
  local raw="${1:-}"
  local default_value="${2:-1}"
  local value
  if [[ -z "${raw}" ]] || ! [[ "${raw}" =~ ^-?[0-9]+$ ]]; then
    value="${default_value}"
  else
    value="${raw}"
  fi
  if (( value < DOWNLOAD_CONCURRENCY_MIN )); then
    value="${DOWNLOAD_CONCURRENCY_MIN}"
  fi
  if (( value > DOWNLOAD_CONCURRENCY_MAX )); then
    value="${DOWNLOAD_CONCURRENCY_MAX}"
  fi
  echo "${value}"
}

clamp_role_worker_concurrency() {
  local raw="${1:-}"
  local default_value="${2:-1}"
  local min_value="${3:-0}"
  local value
  if [[ -z "${raw}" ]] || ! [[ "${raw}" =~ ^-?[0-9]+$ ]]; then
    value="${default_value}"
  else
    value="${raw}"
  fi
  if (( value < min_value )); then
    value="${min_value}"
  fi
  if (( value > ROLE_WORKER_CONCURRENCY_MAX )); then
    value="${ROLE_WORKER_CONCURRENCY_MAX}"
  fi
  echo "${value}"
}

start_download_workers() {
  local provider="${1}"
  local role="${2}"
  local configured="${3:-}"
  local default_value="${4:-1}"
  local count
  count="$(clamp_download_concurrency "${configured}" "${default_value}")"
  echo "[entrypoint] starting ${count} ${role} worker(s)"
  local i
  for ((i=1; i<=count; i++)); do
    start env WORKER_ROLE="${role}" python -m raelyn.worker
  done
}

start_role_workers() {
  local role="${1}"
  local configured="${2:-}"
  local default_value="${3:-1}"
  local min_value="${4:-0}"
  local count
  count="$(clamp_role_worker_concurrency "${configured}" "${default_value}" "${min_value}")"
  echo "[entrypoint] starting ${count} ${role} worker(s)"
  local i
  for ((i=1; i<=count; i++)); do
    start env WORKER_ROLE="${role}" python -m raelyn.worker
  done
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
start_download_workers "youtube" "download_youtube" "${YOUTUBE_DOWNLOAD_CONCURRENCY:-}" "${YOUTUBE_DOWNLOAD_CONCURRENCY_DEFAULT}"
start_download_workers "bilibili" "download_bilibili" "${BILIBILI_DOWNLOAD_CONCURRENCY:-}" "${BILIBILI_DOWNLOAD_CONCURRENCY_DEFAULT}"
start env WORKER_ROLE=audio python -m raelyn.worker
start env WORKER_ROLE=process python -m raelyn.worker
start_role_workers "asr" "${ASR_WORKER_CONCURRENCY:-}" "${ASR_WORKER_CONCURRENCY_DEFAULT}" "${ASR_WORKER_CONCURRENCY_MIN}"
start env WORKER_ROLE=sync python -m raelyn.worker
start_role_workers "embedding" "${EMBEDDING_WORKER_CONCURRENCY:-}" "${EMBEDDING_WORKER_CONCURRENCY_DEFAULT}" "${EMBEDDING_WORKER_CONCURRENCY_MIN}"
start_role_workers "analysis" "${ANALYSIS_WORKER_CONCURRENCY:-}" "${ANALYSIS_WORKER_CONCURRENCY_DEFAULT}" "${ANALYSIS_WORKER_CONCURRENCY_MIN}"
start env WORKER_ROLE=ai python -m raelyn.worker

# Scheduler
start python -m raelyn.scheduler

wait -n
shutdown
exit 1
