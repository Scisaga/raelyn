#!/usr/bin/env bash
set -euo pipefail

if command -v node >/dev/null 2>&1; then
  exec node "$@"
fi

WIN_NODE="/mnt/c/Program Files/nodejs/node.exe"
if [[ -x "$WIN_NODE" ]]; then
  exec "$WIN_NODE" "$@"
fi

echo "node not found." >&2
echo "Fix: install Node in WSL, or install Node on Windows (node.exe) and run inside WSL with /mnt/c mounted." >&2
exit 1

