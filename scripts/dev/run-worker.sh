#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

usage() {
  cat <<'EOF'
Usage: ./scripts/dev/run-worker.sh [role]

Roles:
  download
  download_youtube
  download_bilibili
  audio
  process
  asr
  sync
  ai

If role is omitted, WORKER_ROLE from the environment is used as-is.
EOF
}

requested_role="${1:-}"
if [[ "${requested_role:-}" == "-h" || "${requested_role:-}" == "--help" || "${requested_role:-}" == "help" ]]; then
  usage
  exit 0
fi

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source scripts/dev/load-env.sh
fi

effective_role="${requested_role:-${WORKER_ROLE:-}}"
if [[ -n "${effective_role:-}" ]]; then
  case "$effective_role" in
    download|download_youtube|download_bilibili|audio|process|asr|sync|ai)
      export WORKER_ROLE="$effective_role"
      ;;
    *)
      echo "invalid worker role: $effective_role" >&2
      usage >&2
      exit 2
      ;;
  esac
fi

export PYTHONPATH="$(pwd)/backend"
PY="${PY:-python3}"
if [[ -x .venv/bin/python ]]; then PY=".venv/bin/python"; fi
exec "$PY" -m raelyn.worker
