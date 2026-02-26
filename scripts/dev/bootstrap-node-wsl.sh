#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

NODE_VERSION="${NODE_VERSION:-20.11.1}"
NVM_VERSION="${NVM_VERSION:-v0.40.1}"

unset NODE_TLS_REJECT_UNAUTHORIZED || true

if command -v npm >/dev/null 2>&1; then
  NPM_PATH="$(command -v npm || true)"
  if [[ "$NPM_PATH" == /mnt/c/* ]]; then
    echo "[node] warning: detected Windows npm in WSL PATH: $NPM_PATH" >&2
    echo "[node] recommended: use nvm node/npm inside WSL (this script will install it)." >&2
  fi
fi

echo "[node] installing nvm ${NVM_VERSION} (user space)"
if [[ ! -d "$HOME/.nvm" ]]; then
  curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/${NVM_VERSION}/install.sh" | bash
fi

export NVM_DIR="$HOME/.nvm"
# shellcheck disable=SC1091
source "$NVM_DIR/nvm.sh"

echo "[node] installing node ${NODE_VERSION}"
nvm install "${NODE_VERSION}"
nvm use "${NODE_VERSION}"

echo "[node] node: $(node -v)"
echo "[node] npm : $(npm -v)"

echo "[node] done. Tip: restart your shell, or run:"
echo "  export NVM_DIR=\"$HOME/.nvm\" && . \"$HOME/.nvm/nvm.sh\""
