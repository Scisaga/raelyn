#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

echo "[ui] install deps + build"

unset NODE_TLS_REJECT_UNAUTHORIZED || true

if ! command -v npm >/dev/null 2>&1; then
  echo "[ui] npm not found. Fix: install Node.js (recommended via nvm in WSL) then re-run." >&2
  exit 1
fi

NPM_PATH="$(command -v npm || true)"
if [[ "$NPM_PATH" == /mnt/c/* ]]; then
  echo "[ui] Detected Windows npm inside WSL: $NPM_PATH" >&2
  echo "[ui] This often fails due to UNC/WSL path translation when running scripts." >&2
  echo "[ui] Fix options:" >&2
  echo "  A) Install Node inside WSL (recommended): ./scripts/dev/bootstrap-node-wsl.sh" >&2
  echo "  B) Run UI build from Windows terminal on a Windows path repo copy" >&2
  exit 1
fi

needs_npm_ci() {
  if [[ "${FORCE_UI_NPM_CI:-}" == "1" ]]; then
    return 0
  fi

  if [[ ! -d ui/node_modules ]]; then
    return 0
  fi

  # `ui/node_modules` may exist but be incomplete after a partial copy or
  # production-only install. Verify a required build dependency is installed.
  if ! npm -C ui ls esbuild --depth=0 >/dev/null 2>&1; then
    return 0
  fi

  return 1
}

if needs_npm_ci; then
  echo "[ui] installing dependencies with npm ci"
  npm -C ui ci
else
  echo "[ui] deps ok (required build deps installed); skip npm ci"
fi
npm -C ui run ui:build
