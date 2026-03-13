#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

PY="${PY:-python3}"
if [[ -x .venv/bin/python ]]; then PY=".venv/bin/python"; fi

echo "[install] python: $($PY --version)"
echo "[install] pip: $($PY -m pip --version)"

ytdlp_pkg="${YTDLP_PKG:-yt-dlp[default]}"
ytdlp_ver="${YTDLP_VERSION:-}"
ejs_pkg="${YTDLP_EJS_PKG:-yt-dlp-ejs}"
ejs_ver="${YTDLP_EJS_VERSION:-}"

packages=()

if [[ -n "${ytdlp_ver}" ]]; then
  echo "[install] ${ytdlp_pkg}==${ytdlp_ver}"
  packages+=("${ytdlp_pkg}==${ytdlp_ver}")
else
  echo "[install] ${ytdlp_pkg} (unpinned; set YTDLP_VERSION to pin)"
  packages+=("${ytdlp_pkg}")
fi

if [[ -n "${ejs_ver}" ]]; then
  echo "[install] ${ejs_pkg}==${ejs_ver}"
  packages+=("${ejs_pkg}==${ejs_ver}")
else
  echo "[install] ${ejs_pkg} (unpinned; set YTDLP_EJS_VERSION to pin)"
  packages+=("${ejs_pkg}")
fi

$PY -m pip install -U "${packages[@]}"

$PY - <<'PY'
import importlib
import importlib.metadata
import yt_dlp

print('[install] ok: yt_dlp', yt_dlp.version.__version__)
try:
    m = importlib.import_module('yt_dlp_ejs')
    ver = getattr(m, '__version__', None) or importlib.metadata.version('yt-dlp-ejs')
    print('[install] ok: yt_dlp_ejs', ver)
except Exception as exc:
    print('[install] warn: failed to import yt_dlp_ejs:', exc)
PY

echo "[install] pip show: yt-dlp / yt-dlp-ejs"
$PY -m pip show yt-dlp yt-dlp-ejs || true

if [[ -x ./bin/node ]]; then
  echo "[install] node (local): $(./bin/node --version || true)"
elif command -v node >/dev/null 2>&1; then
  echo "[install] node (system): $(node --version || true)"
else
  echo "[install] warn: node not found (YouTube EJS/JS challenge solving may fail)" >&2
fi
