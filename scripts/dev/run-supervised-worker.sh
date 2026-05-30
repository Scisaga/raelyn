#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

role="${1:-}"
name="${2:-worker-${role:-unknown}}"

if [[ -z "${role}" ]]; then
  echo "Usage: ./scripts/dev/run-supervised-worker.sh <role> [name]" >&2
  exit 2
fi

restart_delay="${DEV_WORKER_RESTART_DELAY_SECONDS:-${WORKER_RESTART_DELAY_SECONDS:-5}}"
case "$restart_delay" in
  ''|*[!0-9]*) restart_delay=5 ;;
esac

stopping=0
child_pid=""

stop_child() {
  stopping=1
  if [[ -n "${child_pid}" ]] && kill -0 "${child_pid}" >/dev/null 2>&1; then
    kill "${child_pid}" >/dev/null 2>&1 || true
    wait "${child_pid}" >/dev/null 2>&1 || true
  fi
}

trap stop_child TERM INT

attempt=0
while [[ "${stopping}" -eq 0 ]]; do
  attempt=$((attempt + 1))
  echo "[supervisor] ${name}: starting role=${role} attempt=${attempt}"
  bash scripts/dev/run-worker.sh "${role}" &
  child_pid="$!"

  set +e
  wait "${child_pid}"
  exit_code="$?"
  set -e
  child_pid=""

  if [[ "${stopping}" -ne 0 ]]; then
    break
  fi

  echo "[supervisor] ${name}: worker exited code=${exit_code}; restart in ${restart_delay}s"
  sleep "${restart_delay}"
done

echo "[supervisor] ${name}: stopped"
