#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

PID_DIR="tmp/pids"
LOG_DIR="tmp/logs"

API_PID_FILE="${PID_DIR}/api.pid"
WORKER_YT_DL_PID_FILE="${PID_DIR}/worker-download-youtube.pid"
WORKER_BILI_DL_PID_FILE="${PID_DIR}/worker-download-bilibili.pid"
WORKER_AUDIO_PID_FILE="${PID_DIR}/worker-audio.pid"
WORKER_PROCESS_PID_FILE="${PID_DIR}/worker-process.pid"
WORKER_ASR_PID_FILE="${PID_DIR}/worker-asr.pid"
WORKER_SYNC_PID_FILE="${PID_DIR}/worker-sync.pid"
WORKER_AI_PID_FILE="${PID_DIR}/worker-ai.pid"
SCHED_PID_FILE="${PID_DIR}/scheduler.pid"
MCP_PID_FILE="${PID_DIR}/mcp.pid"

API_LOG="${LOG_DIR}/api.log"
WORKER_YT_DL_LOG="${LOG_DIR}/worker-download-youtube.log"
WORKER_BILI_DL_LOG="${LOG_DIR}/worker-download-bilibili.log"
WORKER_AUDIO_LOG="${LOG_DIR}/worker-audio.log"
WORKER_PROCESS_LOG="${LOG_DIR}/worker-process.log"
WORKER_ASR_LOG="${LOG_DIR}/worker-asr.log"
WORKER_SYNC_LOG="${LOG_DIR}/worker-sync.log"
WORKER_AI_LOG="${LOG_DIR}/worker-ai.log"
SCHED_LOG="${LOG_DIR}/scheduler.log"
MCP_LOG="${LOG_DIR}/mcp.log"

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
  start     Start api/worker/scheduler/mcp in background
  stop      Stop all started processes
  restart   Stop then start
  reset     Stop + clear DB/S3 (DANGEROUS)
  status    Show running status + pids
  logs      Tail logs (api/workers/scheduler/mcp)

Notes:
  - Uses pidfiles under tmp/pids/ and logs under tmp/logs/
  - Uses existing run scripts:
      scripts/dev/run-api.sh
      scripts/dev/run-worker-download-youtube.sh
      scripts/dev/run-worker-download-bilibili.sh
      scripts/dev/run-worker-audio.sh
      scripts/dev/run-worker-process.sh
      scripts/dev/run-worker-asr.sh
      scripts/dev/run-worker-sync.sh
      scripts/dev/run-worker-ai.sh
      scripts/dev/run-scheduler.sh
      scripts/dev/run-mcp.sh
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
  # Kill any leftover raelyn processes not managed by pidfiles.
  # This commonly happens if the user started servers manually.
  local patterns=(
    "python.*-m raelyn\\.api_server"
    "python.*-m raelyn\\.worker"
    "python.*-m raelyn\\.scheduler"
    "python.*-m raelyn\\.mcp_server"
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
  echo "[stop] stray raelyn pids: ${uniq[*]}"
  kill "${uniq[@]}" >/dev/null 2>&1 || true
  sleep 0.2
  kill -9 "${uniq[@]}" >/dev/null 2>&1 || true
}

do_status() {
  local api_pid yt_dl_pid bili_dl_pid audio_pid process_pid asr_pid sync_pid ai_pid sched_pid mcp_pid
  api_pid="$(read_pid "$API_PID_FILE")"
  yt_dl_pid="$(read_pid "$WORKER_YT_DL_PID_FILE")"
  bili_dl_pid="$(read_pid "$WORKER_BILI_DL_PID_FILE")"
  audio_pid="$(read_pid "$WORKER_AUDIO_PID_FILE")"
  process_pid="$(read_pid "$WORKER_PROCESS_PID_FILE")"
  asr_pid="$(read_pid "$WORKER_ASR_PID_FILE")"
  sync_pid="$(read_pid "$WORKER_SYNC_PID_FILE")"
  ai_pid="$(read_pid "$WORKER_AI_PID_FILE")"
  sched_pid="$(read_pid "$SCHED_PID_FILE")"
  mcp_pid="$(read_pid "$MCP_PID_FILE")"

  if is_running "$api_pid"; then
    echo "[status] api: running pid=${api_pid} log=${API_LOG}"
  else
    echo "[status] api: stopped"
  fi

  if is_running "$yt_dl_pid"; then
    echo "[status] worker-download-youtube: running pid=${yt_dl_pid} log=${WORKER_YT_DL_LOG}"
  else
    echo "[status] worker-download-youtube: stopped"
  fi

  if is_running "$bili_dl_pid"; then
    echo "[status] worker-download-bilibili: running pid=${bili_dl_pid} log=${WORKER_BILI_DL_LOG}"
  else
    echo "[status] worker-download-bilibili: stopped"
  fi

  if is_running "$audio_pid"; then
    echo "[status] worker-audio: running pid=${audio_pid} log=${WORKER_AUDIO_LOG}"
  else
    echo "[status] worker-audio: stopped"
  fi

  if is_running "$process_pid"; then
    echo "[status] worker-process: running pid=${process_pid} log=${WORKER_PROCESS_LOG}"
  else
    echo "[status] worker-process: stopped"
  fi

  if is_running "$asr_pid"; then
    echo "[status] worker-asr: running pid=${asr_pid} log=${WORKER_ASR_LOG}"
  else
    echo "[status] worker-asr: stopped"
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

  if is_running "$mcp_pid"; then
    echo "[status] mcp: running pid=${mcp_pid} log=${MCP_LOG}"
  else
    echo "[status] mcp: stopped"
  fi
}

cmd="${1:-}"
case "$cmd" in
  start)
    ensure_ui_built
    start_one "api" "$API_PID_FILE" "$API_LOG" bash scripts/dev/run-api.sh
    start_one "worker-download-youtube" "$WORKER_YT_DL_PID_FILE" "$WORKER_YT_DL_LOG" bash scripts/dev/run-worker-download-youtube.sh
    start_one "worker-download-bilibili" "$WORKER_BILI_DL_PID_FILE" "$WORKER_BILI_DL_LOG" bash scripts/dev/run-worker-download-bilibili.sh
    start_one "worker-audio" "$WORKER_AUDIO_PID_FILE" "$WORKER_AUDIO_LOG" bash scripts/dev/run-worker-audio.sh
    start_one "worker-process" "$WORKER_PROCESS_PID_FILE" "$WORKER_PROCESS_LOG" bash scripts/dev/run-worker-process.sh
    start_one "worker-asr" "$WORKER_ASR_PID_FILE" "$WORKER_ASR_LOG" bash scripts/dev/run-worker-asr.sh
    start_one "worker-sync" "$WORKER_SYNC_PID_FILE" "$WORKER_SYNC_LOG" bash scripts/dev/run-worker-sync.sh
    start_one "worker-ai" "$WORKER_AI_PID_FILE" "$WORKER_AI_LOG" bash scripts/dev/run-worker-ai.sh
    start_one "scheduler" "$SCHED_PID_FILE" "$SCHED_LOG" bash scripts/dev/run-scheduler.sh
    if [[ -n "${MCP_BEARER_TOKEN:-}" ]]; then
      start_one "mcp" "$MCP_PID_FILE" "$MCP_LOG" bash scripts/dev/run-mcp.sh
    else
      echo "[start] mcp: skip (MCP_BEARER_TOKEN is empty)"
    fi
    do_status
    ;;
  stop)
    stop_one "mcp" "$MCP_PID_FILE"
    stop_one "scheduler" "$SCHED_PID_FILE"
    stop_one "worker-ai" "$WORKER_AI_PID_FILE"
    stop_one "worker-sync" "$WORKER_SYNC_PID_FILE"
    stop_one "worker-asr" "$WORKER_ASR_PID_FILE"
    stop_one "worker-process" "$WORKER_PROCESS_PID_FILE"
    stop_one "worker-audio" "$WORKER_AUDIO_PID_FILE"
    stop_one "worker-download-bilibili" "$WORKER_BILI_DL_PID_FILE"
    stop_one "worker-download-youtube" "$WORKER_YT_DL_PID_FILE"
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
    echo "[logs] tail -f ${API_LOG} ${WORKER_YT_DL_LOG} ${WORKER_BILI_DL_LOG} ${WORKER_AUDIO_LOG} ${WORKER_PROCESS_LOG} ${WORKER_ASR_LOG} ${WORKER_SYNC_LOG} ${WORKER_AI_LOG} ${SCHED_LOG} ${MCP_LOG}"
    tail -n 200 -f "$API_LOG" "$WORKER_YT_DL_LOG" "$WORKER_BILI_DL_LOG" "$WORKER_AUDIO_LOG" "$WORKER_PROCESS_LOG" "$WORKER_ASR_LOG" "$WORKER_SYNC_LOG" "$WORKER_AI_LOG" "$SCHED_LOG" "$MCP_LOG"
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
