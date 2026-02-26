#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

if [[ ! -f .env ]]; then
  echo ".env not found; create it from .env.example" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

