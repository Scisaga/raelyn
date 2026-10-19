#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

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

while [[ "${stopping}" -eq 0 ]]; do
  echo "[supervisor] scheduler: starting"
  bash scripts/dev/run-scheduler.sh &
  child_pid="$!"
  set +e
  wait "${child_pid}"
  exit_code="$?"
  set -e
  child_pid=""
  if [[ "${stopping}" -ne 0 ]]; then
    break
  fi
  echo "[supervisor] scheduler: exited code=${exit_code}; restart in 5s"
  sleep 5
done
