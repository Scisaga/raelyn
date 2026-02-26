#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

PID_DIR="tmp/pids"
LOG_DIR="tmp/logs"

API_PID_FILE="${PID_DIR}/api.pid"
WORKER_DL_PID_FILE="${PID_DIR}/worker-download.pid"
WORKER_PROCESS_PID_FILE="${PID_DIR}/worker-process.pid"
WORKER_SYNC_PID_FILE="${PID_DIR}/worker-sync.pid"
WORKER_AI_PID_FILE="${PID_DIR}/worker-ai.pid"
SCHED_PID_FILE="${PID_DIR}/scheduler.pid"

API_LOG="${LOG_DIR}/api.log"
WORKER_DL_LOG="${LOG_DIR}/worker-download.log"
WORKER_PROCESS_LOG="${LOG_DIR}/worker-process.log"
WORKER_SYNC_LOG="${LOG_DIR}/worker-sync.log"
WORKER_AI_LOG="${LOG_DIR}/worker-ai.log"
SCHED_LOG="${LOG_DIR}/scheduler.log"

mkdir -p "$PID_DIR" "$LOG_DIR"

ensure_ui_built() {
  if [[ "${SKIP_UI_BUILD:-}" == "1" ]]; then
    echo "[ui] SKIP_UI_BUILD=1; skip ui build"
    return 0
  fi
  if [[ ! -f ui/package.json ]]; then
    return 0
  fi
  echo "[ui] building ui (static/index.html, css, vendor)…"
  bash scripts/dev/build-ui.sh
}

usage() {
  cat <<'EOF'
Usage: ./scripts/dev/devctl.sh <command>

Commands:
  start     Start api/worker/scheduler in background
  stop      Stop all started processes
  restart   Stop then start
  reset     Stop + clear DB/S3 (DANGEROUS)
  status    Show running status + pids
  logs      Tail logs (api/workers/scheduler)

Notes:
  - Uses pidfiles under tmp/pids/ and logs under tmp/logs/
  - Uses existing run scripts:
      scripts/dev/run-api.sh
      scripts/dev/run-worker-download.sh
      scripts/dev/run-worker-process.sh
      scripts/dev/run-worker-sync.sh
      scripts/dev/run-worker-ai.sh
      scripts/dev/run-scheduler.sh
  - Reset uses:
      scripts/dev/reset-data.sh --yes
EOF
}

is_running() {
  local pid="$1"
  if [[ -z "${pid:-}" ]]; then
    return 1
  fi
  kill -0 "$pid" >/dev/null 2>&1
}

read_pid() {
  local file="$1"
  if [[ -f "$file" ]]; then
    tr -d '\n' <"$file" || true
  fi
}

write_pid() {
  local file="$1"
  local pid="$2"
  echo -n "$pid" >"$file"
}

start_one() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"
  shift 3
  local cmd=( "$@" )

  local existing
  existing="$(read_pid "$pid_file")"
  if is_running "$existing"; then
    echo "[start] ${name}: already running (pid=${existing})"
    return 0
  fi
  rm -f "$pid_file" >/dev/null 2>&1 || true

  echo "[start] ${name}: ${cmd[*]}"
  : >"$log_file"
  nohup "${cmd[@]}" >>"$log_file" 2>&1 &
  local pid="$!"
  write_pid "$pid_file" "$pid"
  echo "[start] ${name}: pid=${pid} log=${log_file}"
}

stop_one() {
  local name="$1"
  local pid_file="$2"

  local pid
  pid="$(read_pid "$pid_file")"
  if ! is_running "$pid"; then
    echo "[stop] ${name}: not running"
    rm -f "$pid_file" >/dev/null 2>&1 || true
    return 0
  fi

  echo "[stop] ${name}: SIGTERM pid=${pid}"
  kill "$pid" >/dev/null 2>&1 || true
  # Also try to stop the whole process group (helps terminate yt-dlp/ffmpeg children).
  kill -TERM "-${pid}" >/dev/null 2>&1 || true

  local i
  for i in {1..50}; do
    if ! is_running "$pid"; then
      rm -f "$pid_file" >/dev/null 2>&1 || true
      echo "[stop] ${name}: stopped"
      return 0
    fi
    sleep 0.1
  done

  echo "[stop] ${name}: SIGKILL pid=${pid}"
  kill -9 "$pid" >/dev/null 2>&1 || true
  kill -9 "-${pid}" >/dev/null 2>&1 || true
  rm -f "$pid_file" >/dev/null 2>&1 || true
}

kill_strays() {
  # Kill any leftover videosync processes not managed by pidfiles.
  # This commonly happens if the user started servers manually.
  local patterns=(
    "python.*-m videosync\\.api_server"
    "python.*-m videosync\\.worker"
    "python.*-m videosync\\.scheduler"
  )
  local pids=()
  local pat
  for pat in "${patterns[@]}"; do
    while IFS= read -r pid; do
      [[ -n "${pid:-}" ]] && pids+=( "$pid" )
    done < <(pgrep -f "$pat" 2>/dev/null || true)
  done
  if [[ "${#pids[@]}" -eq 0 ]]; then
    return 0
  fi
  # De-duplicate
  local uniq=()
  local seen=" "
  local x
  for x in "${pids[@]}"; do
    if [[ "$seen" != *" $x "* ]]; then
      uniq+=( "$x" )
      seen+=" $x "
    fi
  done
  echo "[stop] stray videosync pids: ${uniq[*]}"
  kill "${uniq[@]}" >/dev/null 2>&1 || true
  sleep 0.2
  kill -9 "${uniq[@]}" >/dev/null 2>&1 || true
}

do_status() {
  local api_pid dl_pid process_pid sync_pid ai_pid sched_pid
  api_pid="$(read_pid "$API_PID_FILE")"
  dl_pid="$(read_pid "$WORKER_DL_PID_FILE")"
  process_pid="$(read_pid "$WORKER_PROCESS_PID_FILE")"
  sync_pid="$(read_pid "$WORKER_SYNC_PID_FILE")"
  ai_pid="$(read_pid "$WORKER_AI_PID_FILE")"
  sched_pid="$(read_pid "$SCHED_PID_FILE")"

  if is_running "$api_pid"; then
    echo "[status] api: running pid=${api_pid} log=${API_LOG}"
  else
    echo "[status] api: stopped"
  fi

  if is_running "$dl_pid"; then
    echo "[status] worker-download: running pid=${dl_pid} log=${WORKER_DL_LOG}"
  else
    echo "[status] worker-download: stopped"
  fi

  if is_running "$process_pid"; then
    echo "[status] worker-process: running pid=${process_pid} log=${WORKER_PROCESS_LOG}"
  else
    echo "[status] worker-process: stopped"
  fi

  if is_running "$sync_pid"; then
    echo "[status] worker-sync: running pid=${sync_pid} log=${WORKER_SYNC_LOG}"
  else
    echo "[status] worker-sync: stopped"
  fi

  if is_running "$ai_pid"; then
    echo "[status] worker-ai: running pid=${ai_pid} log=${WORKER_AI_LOG}"
  else
    echo "[status] worker-ai: stopped"
  fi

  if is_running "$sched_pid"; then
    echo "[status] scheduler: running pid=${sched_pid} log=${SCHED_LOG}"
  else
    echo "[status] scheduler: stopped"
  fi
}

cmd="${1:-}"
case "$cmd" in
  start)
    ensure_ui_built
    start_one "api" "$API_PID_FILE" "$API_LOG" bash scripts/dev/run-api.sh
    start_one "worker-download" "$WORKER_DL_PID_FILE" "$WORKER_DL_LOG" bash scripts/dev/run-worker-download.sh
    start_one "worker-process" "$WORKER_PROCESS_PID_FILE" "$WORKER_PROCESS_LOG" bash scripts/dev/run-worker-process.sh
    start_one "worker-sync" "$WORKER_SYNC_PID_FILE" "$WORKER_SYNC_LOG" bash scripts/dev/run-worker-sync.sh
    start_one "worker-ai" "$WORKER_AI_PID_FILE" "$WORKER_AI_LOG" bash scripts/dev/run-worker-ai.sh
    start_one "scheduler" "$SCHED_PID_FILE" "$SCHED_LOG" bash scripts/dev/run-scheduler.sh
    do_status
    ;;
  stop)
    stop_one "scheduler" "$SCHED_PID_FILE"
    stop_one "worker-ai" "$WORKER_AI_PID_FILE"
    stop_one "worker-sync" "$WORKER_SYNC_PID_FILE"
    stop_one "worker-process" "$WORKER_PROCESS_PID_FILE"
    stop_one "worker-download" "$WORKER_DL_PID_FILE"
    stop_one "api" "$API_PID_FILE"
    kill_strays
    do_status
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  reset)
    if [[ "${2:-}" != "--yes" ]]; then
      echo "[reset] Refusing to run without --yes (this will DELETE DB + S3 bucket objects)." >&2
      exit 2
    fi
    "$0" stop
    bash scripts/dev/reset-data.sh --yes
    ;;
  status)
    do_status
    ;;
  logs)
    echo "[logs] tail -f ${API_LOG} ${WORKER_DL_LOG} ${WORKER_PROCESS_LOG} ${WORKER_SYNC_LOG} ${WORKER_AI_LOG} ${SCHED_LOG}"
    tail -n 200 -f "$API_LOG" "$WORKER_DL_LOG" "$WORKER_PROCESS_LOG" "$WORKER_SYNC_LOG" "$WORKER_AI_LOG" "$SCHED_LOG"
    ;;
  -h|--help|help|"")
    usage
    exit 0
    ;;
  *)
    echo "unknown command: $cmd" >&2
    usage >&2
    exit 2
    ;;
esac
