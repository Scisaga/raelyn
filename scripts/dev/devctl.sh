#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source scripts/dev/load-env.sh
fi

PID_DIR="tmp/pids"
LOG_DIR="tmp/logs"
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

API_PID_FILE="${PID_DIR}/api.pid"
WORKER_AUDIO_PID_FILE="${PID_DIR}/worker-audio.pid"
WORKER_PROCESS_PID_FILE="${PID_DIR}/worker-process.pid"
WORKER_ASR_PID_FILE="${PID_DIR}/worker-asr.pid"
WORKER_SYNC_PID_FILE="${PID_DIR}/worker-sync.pid"
WORKER_AI_PID_FILE="${PID_DIR}/worker-ai.pid"
SCHED_PID_FILE="${PID_DIR}/scheduler.pid"

API_LOG="${LOG_DIR}/api.log"
WORKER_AUDIO_LOG="${LOG_DIR}/worker-audio.log"
WORKER_PROCESS_LOG="${LOG_DIR}/worker-process.log"
WORKER_ASR_LOG="${LOG_DIR}/worker-asr.log"
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

download_worker_count() {
  local provider="$1"
  case "$provider" in
    youtube)
      clamp_download_concurrency "${YOUTUBE_DOWNLOAD_CONCURRENCY:-}" "${YOUTUBE_DOWNLOAD_CONCURRENCY_DEFAULT}"
      ;;
    bilibili)
      clamp_download_concurrency "${BILIBILI_DOWNLOAD_CONCURRENCY:-}" "${BILIBILI_DOWNLOAD_CONCURRENCY_DEFAULT}"
      ;;
    *)
      echo "1"
      ;;
  esac
}

download_worker_role() {
  local provider="$1"
  case "$provider" in
    youtube) echo "download_youtube" ;;
    bilibili) echo "download_bilibili" ;;
    *) return 1 ;;
  esac
}

download_worker_name() {
  local provider="$1"
  local index="$2"
  echo "worker-download-${provider}-${index}"
}

download_worker_pid_file() {
  local provider="$1"
  local index="$2"
  echo "${PID_DIR}/worker-download-${provider}-${index}.pid"
}

download_worker_log_file() {
  local provider="$1"
  local index="$2"
  echo "${LOG_DIR}/worker-download-${provider}-${index}.log"
}

download_worker_legacy_pid_file() {
  local provider="$1"
  echo "${PID_DIR}/worker-download-${provider}.pid"
}

download_worker_legacy_log_file() {
  local provider="$1"
  echo "${LOG_DIR}/worker-download-${provider}.log"
}

download_worker_indices() {
  local provider="$1"
  local count
  count="$(download_worker_count "$provider")"
  local values=()
  local i
  for ((i=1; i<=count; i++)); do
    values+=( "$i" )
  done
  local path
  for path in "${PID_DIR}/worker-download-${provider}-"*.pid; do
    [[ -e "$path" ]] || continue
    local name="${path##*/}"
    local idx="${name#worker-download-${provider}-}"
    idx="${idx%.pid}"
    if [[ "$idx" =~ ^[0-9]+$ ]]; then
      values+=( "$idx" )
    fi
  done
  if [[ -f "$(download_worker_legacy_pid_file "$provider")" ]]; then
    values+=( "legacy" )
  fi

  local uniq=()
  local seen=" "
  local item
  for item in "${values[@]}"; do
    if [[ "$seen" == *" ${item} "* ]]; then
      continue
    fi
    uniq+=( "$item" )
    seen+=" ${item} "
  done

  local numeric=()
  local has_legacy=0
  for item in "${uniq[@]}"; do
    if [[ "$item" == "legacy" ]]; then
      has_legacy=1
    else
      numeric+=( "$item" )
    fi
  done
  if [[ "${#numeric[@]}" -gt 0 ]]; then
    IFS=$'\n' numeric=($(printf '%s\n' "${numeric[@]}" | sort -n))
    unset IFS
    printf '%s\n' "${numeric[@]}"
  fi
  if (( has_legacy )); then
    echo "legacy"
  fi
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

scaled_worker_count() {
  local role="$1"
  case "$role" in
    asr)
      clamp_role_worker_concurrency "${ASR_WORKER_CONCURRENCY:-}" "${ASR_WORKER_CONCURRENCY_DEFAULT}" "${ASR_WORKER_CONCURRENCY_MIN}"
      ;;
    embedding)
      clamp_role_worker_concurrency "${EMBEDDING_WORKER_CONCURRENCY:-}" "${EMBEDDING_WORKER_CONCURRENCY_DEFAULT}" "${EMBEDDING_WORKER_CONCURRENCY_MIN}"
      ;;
    analysis)
      clamp_role_worker_concurrency "${ANALYSIS_WORKER_CONCURRENCY:-}" "${ANALYSIS_WORKER_CONCURRENCY_DEFAULT}" "${ANALYSIS_WORKER_CONCURRENCY_MIN}"
      ;;
    *)
      echo "1"
      ;;
  esac
}

scaled_worker_name() {
  local role="$1"
  local index="$2"
  echo "worker-${role}-${index}"
}

scaled_worker_pid_file() {
  local role="$1"
  local index="$2"
  echo "${PID_DIR}/worker-${role}-${index}.pid"
}

scaled_worker_log_file() {
  local role="$1"
  local index="$2"
  echo "${LOG_DIR}/worker-${role}-${index}.log"
}

scaled_worker_indices() {
  local role="$1"
  local count
  count="$(scaled_worker_count "$role")"
  local values=()
  local i
  for ((i=1; i<=count; i++)); do
    values+=( "$i" )
  done
  local path
  for path in "${PID_DIR}/worker-${role}-"*.pid; do
    [[ -e "$path" ]] || continue
    local name="${path##*/}"
    local idx="${name#worker-${role}-}"
    idx="${idx%.pid}"
    if [[ "$idx" =~ ^[0-9]+$ ]]; then
      values+=( "$idx" )
    fi
  done
  if [[ "$role" == "asr" ]] && [[ -f "$WORKER_ASR_PID_FILE" ]]; then
    values+=( "legacy" )
  fi

  local uniq=()
  local seen=" "
  local item
  for item in "${values[@]}"; do
    if [[ "$seen" == *" ${item} "* ]]; then
      continue
    fi
    uniq+=( "$item" )
    seen+=" ${item} "
  done
  local numeric=()
  local has_legacy=0
  for item in "${uniq[@]}"; do
    if [[ "$item" == "legacy" ]]; then
      has_legacy=1
    else
      numeric+=( "$item" )
    fi
  done
  if [[ "${#numeric[@]}" -gt 0 ]]; then
    IFS=$'\n' numeric=($(printf '%s\n' "${numeric[@]}" | sort -n))
    unset IFS
    printf '%s\n' "${numeric[@]}"
  fi
  if (( has_legacy )); then
    echo "legacy"
  fi
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
      scripts/dev/run-worker.sh download_youtube
      scripts/dev/run-worker.sh download_bilibili
      scripts/dev/run-worker.sh audio
      scripts/dev/run-worker.sh process
      scripts/dev/run-worker.sh asr
      scripts/dev/run-worker.sh sync
      scripts/dev/run-worker.sh embedding
      scripts/dev/run-worker.sh analysis
      scripts/dev/run-worker.sh ai
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

wait_for_http_ok() {
  local name="$1"
  local url="$2"
  local pid_file="$3"
  local log_file="$4"
  local timeout_seconds="${5:-20}"
  shift 5
  local pid
  pid="$(read_pid "$pid_file")"

  local i
  for ((i=0; i<timeout_seconds * 2; i++)); do
    if ! is_running "$pid"; then
      echo "[start] ${name}: process exited during startup; inspect ${log_file}" >&2
      return 1
    fi
    local curl_args=(-fsS --max-time 2)
    if [[ "$#" -gt 0 ]]; then
      curl_args+=( "$@" )
    fi
    if curl "${curl_args[@]}" "$url" >/dev/null 2>&1; then
      echo "[start] ${name}: ready ${url}"
      return 0
    fi
    sleep 0.5
  done

  echo "[start] ${name}: not ready after ${timeout_seconds}s; inspect ${log_file}" >&2
  return 1
}

start_worker_role() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"
  local role="$4"
  start_one "$name" "$pid_file" "$log_file" bash scripts/dev/run-supervised-worker.sh "$role" "$name"
}

start_download_workers() {
  local provider="$1"
  local role
  role="$(download_worker_role "$provider")"
  local count
  count="$(download_worker_count "$provider")"
  local legacy_pid_file legacy_pid
  legacy_pid_file="$(download_worker_legacy_pid_file "$provider")"
  legacy_pid="$(read_pid "$legacy_pid_file")"
  local i
  for ((i=1; i<=count; i++)); do
    if (( i == 1 )) && is_running "$legacy_pid"; then
      echo "[start] $(download_worker_name "$provider" "$i"): legacy instance already running (pid=${legacy_pid})"
      continue
    fi
    start_worker_role \
      "$(download_worker_name "$provider" "$i")" \
      "$(download_worker_pid_file "$provider" "$i")" \
      "$(download_worker_log_file "$provider" "$i")" \
      "$role"
  done
}

start_scaled_workers() {
  local role="$1"
  local count
  count="$(scaled_worker_count "$role")"
  local legacy_pid=""
  if [[ "$role" == "asr" ]]; then
    legacy_pid="$(read_pid "$WORKER_ASR_PID_FILE")"
  fi
  local i
  for ((i=1; i<=count; i++)); do
    if [[ "$role" == "asr" ]] && (( i == 1 )) && is_running "$legacy_pid"; then
      echo "[start] $(scaled_worker_name "$role" "$i"): legacy instance already running (pid=${legacy_pid})"
      continue
    fi
    start_worker_role \
      "$(scaled_worker_name "$role" "$i")" \
      "$(scaled_worker_pid_file "$role" "$i")" \
      "$(scaled_worker_log_file "$role" "$i")" \
      "$role"
  done
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

stop_download_workers() {
  local provider="$1"
  local index
  while IFS= read -r index; do
    [[ -n "${index:-}" ]] || continue
    if [[ "$index" == "legacy" ]]; then
      stop_one \
        "worker-download-${provider}" \
        "$(download_worker_legacy_pid_file "$provider")"
    else
      stop_one \
        "$(download_worker_name "$provider" "$index")" \
        "$(download_worker_pid_file "$provider" "$index")"
    fi
  done < <(download_worker_indices "$provider")
}

stop_scaled_workers() {
  local role="$1"
  local index
  while IFS= read -r index; do
    [[ -n "${index:-}" ]] || continue
    if [[ "$role" == "asr" && "$index" == "legacy" ]]; then
      stop_one "worker-asr" "$WORKER_ASR_PID_FILE"
    else
      stop_one \
        "$(scaled_worker_name "$role" "$index")" \
        "$(scaled_worker_pid_file "$role" "$index")"
    fi
  done < <(scaled_worker_indices "$role")
}

kill_strays() {
  # Kill any leftover raelyn processes not managed by pidfiles.
  # This commonly happens if the user started servers manually.
  local patterns=(
    "python.*-m raelyn\\.api_server"
    "python.*-m raelyn\\.worker"
    "python.*-m raelyn\\.scheduler"
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
  local api_pid audio_pid process_pid sync_pid ai_pid sched_pid
  api_pid="$(read_pid "$API_PID_FILE")"
  audio_pid="$(read_pid "$WORKER_AUDIO_PID_FILE")"
  process_pid="$(read_pid "$WORKER_PROCESS_PID_FILE")"
  sync_pid="$(read_pid "$WORKER_SYNC_PID_FILE")"
  ai_pid="$(read_pid "$WORKER_AI_PID_FILE")"
  sched_pid="$(read_pid "$SCHED_PID_FILE")"

  if is_running "$api_pid"; then
    echo "[status] api: running pid=${api_pid} log=${API_LOG}"
  else
    echo "[status] api: stopped"
  fi

  local provider index pid pid_file log_file name role count
  for provider in youtube bilibili; do
    count="$(download_worker_count "$provider")"
    role="$(download_worker_role "$provider")"
    echo "[status] ${role}: configured_concurrency=${count}"
    while IFS= read -r index; do
      [[ -n "${index:-}" ]] || continue
      if [[ "$index" == "legacy" ]]; then
        name="worker-download-${provider}"
        pid_file="$(download_worker_legacy_pid_file "$provider")"
        log_file="$(download_worker_legacy_log_file "$provider")"
      else
        name="$(download_worker_name "$provider" "$index")"
        pid_file="$(download_worker_pid_file "$provider" "$index")"
        log_file="$(download_worker_log_file "$provider" "$index")"
      fi
      pid="$(read_pid "$pid_file")"
      if is_running "$pid"; then
        echo "[status] ${name}: running pid=${pid} log=${log_file}"
      else
        echo "[status] ${name}: stopped"
      fi
    done < <(download_worker_indices "$provider")
  done

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

  if is_running "$sync_pid"; then
    echo "[status] worker-sync: running pid=${sync_pid} log=${WORKER_SYNC_LOG}"
  else
    echo "[status] worker-sync: stopped"
  fi

  local scaled_role scaled_index scaled_pid scaled_pid_file scaled_log_file scaled_count
  for scaled_role in asr embedding analysis; do
    scaled_count="$(scaled_worker_count "$scaled_role")"
    echo "[status] worker-${scaled_role}: configured_concurrency=${scaled_count}"
    while IFS= read -r scaled_index; do
      [[ -n "${scaled_index:-}" ]] || continue
      if [[ "$scaled_role" == "asr" && "$scaled_index" == "legacy" ]]; then
        scaled_pid_file="$WORKER_ASR_PID_FILE"
        scaled_log_file="$WORKER_ASR_LOG"
      else
        scaled_pid_file="$(scaled_worker_pid_file "$scaled_role" "$scaled_index")"
        scaled_log_file="$(scaled_worker_log_file "$scaled_role" "$scaled_index")"
      fi
      scaled_pid="$(read_pid "$scaled_pid_file")"
      if is_running "$scaled_pid"; then
        if [[ "$scaled_role" == "asr" && "$scaled_index" == "legacy" ]]; then
          echo "[status] worker-asr: running pid=${scaled_pid} log=${scaled_log_file}"
        else
          echo "[status] $(scaled_worker_name "$scaled_role" "$scaled_index"): running pid=${scaled_pid} log=${scaled_log_file}"
        fi
      else
        if [[ "$scaled_role" == "asr" && "$scaled_index" == "legacy" ]]; then
          echo "[status] worker-asr: stopped"
        else
          echo "[status] $(scaled_worker_name "$scaled_role" "$scaled_index"): stopped"
        fi
      fi
    done < <(scaled_worker_indices "$scaled_role")
  done

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
    api_wait_args=()
    if [[ -n "${API_BEARER_TOKEN:-}" ]]; then
      api_wait_args=(-H "Authorization: Bearer ${API_BEARER_TOKEN}")
    fi
    if ! wait_for_http_ok "api" "http://127.0.0.1:8000/api/health" "$API_PID_FILE" "$API_LOG" 30 "${api_wait_args[@]}"; then
      do_status
      exit 1
    fi
    start_download_workers "youtube"
    start_download_workers "bilibili"
    start_worker_role "worker-audio" "$WORKER_AUDIO_PID_FILE" "$WORKER_AUDIO_LOG" "audio"
    start_worker_role "worker-process" "$WORKER_PROCESS_PID_FILE" "$WORKER_PROCESS_LOG" "process"
    start_worker_role "worker-sync" "$WORKER_SYNC_PID_FILE" "$WORKER_SYNC_LOG" "sync"
    start_scaled_workers "asr"
    start_scaled_workers "embedding"
    start_scaled_workers "analysis"
    start_worker_role "worker-ai" "$WORKER_AI_PID_FILE" "$WORKER_AI_LOG" "ai"
    start_one "scheduler" "$SCHED_PID_FILE" "$SCHED_LOG" bash scripts/dev/run-scheduler.sh
    do_status
    ;;
  stop)
    stop_one "scheduler" "$SCHED_PID_FILE"
    stop_one "worker-ai" "$WORKER_AI_PID_FILE"
    stop_scaled_workers "analysis"
    stop_scaled_workers "embedding"
    stop_scaled_workers "asr"
    stop_one "worker-sync" "$WORKER_SYNC_PID_FILE"
    stop_one "worker-process" "$WORKER_PROCESS_PID_FILE"
    stop_one "worker-audio" "$WORKER_AUDIO_PID_FILE"
    stop_download_workers "bilibili"
    stop_download_workers "youtube"
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
    log_files=("$API_LOG")
    for provider in youtube bilibili; do
      while IFS= read -r index; do
        [[ -n "${index:-}" ]] || continue
        if [[ "$index" == "legacy" ]]; then
          log_files+=("$(download_worker_legacy_log_file "$provider")")
        else
          log_files+=("$(download_worker_log_file "$provider" "$index")")
        fi
      done < <(download_worker_indices "$provider")
    done
    for scaled_role in asr embedding analysis; do
      while IFS= read -r index; do
        [[ -n "${index:-}" ]] || continue
        if [[ "$scaled_role" == "asr" && "$index" == "legacy" ]]; then
          log_files+=("$WORKER_ASR_LOG")
        else
          log_files+=("$(scaled_worker_log_file "$scaled_role" "$index")")
        fi
      done < <(scaled_worker_indices "$scaled_role")
    done
    log_files+=("$WORKER_AUDIO_LOG" "$WORKER_PROCESS_LOG" "$WORKER_SYNC_LOG" "$WORKER_AI_LOG" "$SCHED_LOG")
    touch "${log_files[@]}"
    echo "[logs] tail -f ${log_files[*]}"
    tail -n 200 -f "${log_files[@]}"
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
