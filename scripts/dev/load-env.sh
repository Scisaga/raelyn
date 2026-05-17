#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

if [[ ! -f .env ]]; then
  echo ".env not found; create it from .env.example" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

# Prefer project-local external binaries if present (bin/ffmpeg, bin/node, ...).
export PATH="$(pwd)/bin:${PATH:-}"

# 本机服务地址不能被 shell 里的 HTTP(S)_PROXY 劫持；YouTube 出口只由 YTDLP_PROXY 显式控制。
for host in 127.0.0.1 localhost ::1 host.docker.internal; do
  case ",${NO_PROXY:-}," in
    *,"${host}",*) ;;
    *) export NO_PROXY="${NO_PROXY:+${NO_PROXY},}${host}" ;;
  esac
  case ",${no_proxy:-}," in
    *,"${host}",*) ;;
    *) export no_proxy="${no_proxy:+${no_proxy},}${host}" ;;
  esac
done
