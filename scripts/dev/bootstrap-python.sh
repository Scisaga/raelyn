#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

echo "[bootstrap] python: $(python3 --version)"

print_fix_options() {
  echo "Fix options:" >&2
  echo "  A) Ubuntu/Debian: sudo apt-get update && sudo apt-get install -y python3-venv python3-pip" >&2
  echo "  B) Use Docker: docker compose up --build" >&2
}

if [[ ! -d .venv ]]; then
  echo "[bootstrap] creating venv: .venv"
  if ! python3 -m venv .venv >/dev/null 2>&1; then
    echo "[bootstrap] failed to create venv (python3-venv/ensurepip missing or PEP668)." >&2
    echo "" >&2
    print_fix_options
    exit 1
  fi
fi

VENV_PY="$(pwd)/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "[bootstrap] .venv exists but is incomplete; recreating python entrypoint" >&2
  if ! python3 -m venv .venv >/dev/null 2>&1; then
    echo "[bootstrap] failed to repair .venv." >&2
    echo "" >&2
    print_fix_options
    exit 1
  fi
fi

if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
  echo "[bootstrap] pip missing in .venv, attempting repair via ensurepip"
  if ! "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1; then
    echo "[bootstrap] failed to bootstrap pip inside .venv (ensurepip unavailable)." >&2
    echo "" >&2
    print_fix_options
    exit 1
  fi
fi

echo "[bootstrap] pip: $("$VENV_PY" -m pip --version)"
echo "[bootstrap] installing python deps (venv)"
"$VENV_PY" -m pip install -r backend/requirements.lock.txt

mkdir -p tmp static/css static/vendor
echo "[bootstrap] done"
