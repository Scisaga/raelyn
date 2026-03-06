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
  $PY -m pip install -U "${pkg}==${ver}"
else
  echo "[install] ${pkg} (unpinned; set YTDLP_EJS_VERSION to pin)"
  $PY -m pip install -U "${pkg}"
fi

$PY -c "import importlib; m=importlib.import_module('yt_dlp_ejs'); print('[install] ok: yt_dlp_ejs', getattr(m,'__version__',None))" || true

echo "[install] pip show: yt-dlp / yt-dlp-ejs"
$PY -m pip show yt-dlp yt-dlp-ejs || true

if [[ -x ./bin/node ]]; then
  echo "[install] node (local): $(./bin/node --version || true)"
elif command -v node >/dev/null 2>&1; then
  echo "[install] node (system): $(node --version || true)"
else
  echo "[install] warn: node not found (YouTube EJS/JS challenge solving may fail)" >&2
fi
