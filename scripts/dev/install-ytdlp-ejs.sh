#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

PY="${PY:-python3}"
if [[ -x .venv/bin/python ]]; then PY=".venv/bin/python"; fi

echo "[install] python: $($PY --version)"
echo "[install] pip: $($PY -m pip --version)"

pkg="${YTDLP_EJS_PKG:-yt-dlp-ejs}"
ver="${YTDLP_EJS_VERSION:-}"

if [[ -n "${ver}" ]]; then
  echo "[install] ${pkg}==${ver}"
  $PY -m pip install "${pkg}==${ver}"
else
  echo "[install] ${pkg} (unpinned; set YTDLP_EJS_VERSION to pin)"
  $PY -m pip install "${pkg}"
fi

$PY -c "import importlib; m=importlib.import_module('yt_dlp_ejs'); print('[install] ok: yt_dlp_ejs', getattr(m,'__version__',None))" || true

